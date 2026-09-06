"""
Request processing pipeline for the HTTP Server Tool.

Split out of http_server_tool.py (mechanical move, no behavior change).
_process_request, _sse_response and _extract_bearer_token were previously
HttpServerTool methods; none of them actually depended on other instance
state except self._paused_owners (now an explicit parameter of
_process_request), so they were extracted as free functions instead of
being left behind as artificially-parameterized methods.
"""

import uuid
import inspect
import ipaddress
import urllib.parse
from typing import Optional, Any, Callable, Sequence
from pydantic import BaseModel
from microcoreos import current_identity_var, current_event_id_var
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from tools.http_server.context import HttpContext
from tools.http_server.types import UploadedFile


def _parse_trusted_proxies(raw: str | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma-separated IPs or CIDR networks into ip_network objects."""
    if not raw:
        return []
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item == "*":
            networks.extend([
                ipaddress.ip_network("0.0.0.0/0"),
                ipaddress.ip_network("::/0"),
            ])
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError as e:
            raise ValueError(f"Invalid IP or CIDR in HTTP_TRUSTED_PROXIES: {item!r}") from e
    return networks


def _is_ip_in_networks(
    ip_str: str, networks: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    if not ip_str or not networks:
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False
    return any(ip in net for net in networks)


def _is_valid_ip(ip_str: str) -> bool:
    try:
        ipaddress.ip_address(ip_str.strip())
        return True
    except ValueError:
        return False


def _normalize_origin(origin: str) -> str:
    """Normalize origin string to lowercase {scheme}://{host[:port]} without trailing slash."""
    trimmed = origin.strip()
    parsed = urllib.parse.urlsplit(trimmed)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Malformed origin: {origin!r}")
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https", "ws", "wss"):
        raise ValueError(f"Unsupported origin scheme: {parsed.scheme!r}")
    host = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port
    if (scheme in ("http", "ws") and port == 80) or (scheme in ("https", "wss") and port == 443):
        netloc = host
    elif port is not None:
        netloc = f"{host}:{port}"
    else:
        netloc = host or parsed.netloc.lower()
    return f"{scheme}://{netloc}"


def _parse_ws_origins(raw: str | None) -> set[str]:
    """Parse comma-separated allowed WebSocket origins."""
    if not raw:
        return set()
    origins = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if item == "*":
            raise ValueError("Wildcard '*' is not permitted in HTTP_WS_ORIGINS")
        origins.add(_normalize_origin(item))
    return origins


def _validate_ws_origin(
    websocket: Any,
    policy: str = "off",
    allowed_origins: set[str] | None = None,
    allow_missing: bool = False,
) -> tuple[bool, str | None]:
    """
    Validate WebSocket Origin header against policy.
    Returns (True, None) if allowed, or (False, reason) if rejected.
    """
    if policy == "off":
        return True, None
    if policy != "allowlist":
        return False, f"Unknown WebSocket origin policy: {policy!r}"

    # In allowlist mode:
    origin_headers = []
    if hasattr(websocket.headers, "get_list"):
        origin_headers = websocket.headers.get_list("origin")
    elif hasattr(websocket.headers, "getlist"):
        origin_headers = websocket.headers.getlist("origin")
    elif "origin" in websocket.headers:
        val = websocket.headers.get("origin")
        if val is not None:
            origin_headers = [val]

    # Multiple Origin headers: reject
    if len(origin_headers) > 1:
        return False, "Multiple Origin headers rejected"

    if not origin_headers:
        if allow_missing:
            return True, None
        return False, "Missing Origin header in allowlist mode"

    origin_val = origin_headers[0].strip()
    if not origin_val:
        if allow_missing:
            return True, None
        return False, "Missing Origin header in allowlist mode"

    # Multiple origins in single comma-separated header: reject
    if "," in origin_val:
        return False, "Multiple origins in Origin header rejected"

    # Reject opaque null origin (e.g. sandboxed iframe or file://)
    if origin_val.lower() == "null":
        return False, "Opaque 'null' origin is rejected"

    try:
        normalized = _normalize_origin(origin_val)
    except ValueError as e:
        return False, f"Malformed Origin header: {e}"

    if allowed_origins and normalized in allowed_origins:
        return True, None

    return False, f"Origin {origin_val!r} not in WebSocket allowlist"


def _extract_client_ip(
    request: Request,
    trusted_proxies: Optional[Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]] = None,
    custom_ip_header: Optional[str] = None,
    trust_cloudflare: bool = False,
) -> str | None:
    """
    Extract caller IP for context.client_ip based on configured trusted proxies.

    Security model:
    - If trusted_proxies is unset/empty or the direct socket peer is not in
      trusted_proxies, the direct peer address is returned. Forwarding headers
      from untrusted peers are ignored to prevent spoofing.
    - If the direct peer is in trusted_proxies:
      - If custom_ip_header (e.g. X-Real-IP, True-Client-IP, CF-Connecting-IP)
        is configured and present with a valid IP, it is returned.
      - Standard X-Forwarded-For is evaluated from right to left across trusted
        proxies to locate the first untrusted caller IP.
    """
    peer = request.client.host if request.client else None
    if not peer:
        return None

    if not trusted_proxies:
        return peer

    if not _is_ip_in_networks(peer, trusted_proxies):
        return peer

    # Immediate peer is trusted proxy
    header_name = custom_ip_header or ("Cf-Connecting-Ip" if trust_cloudflare else None)
    if header_name:
        edge_ip = request.headers.get(header_name)
        if edge_ip and _is_valid_ip(edge_ip):
            return edge_ip.strip()

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        for i in range(len(hops) - 1, -1, -1):
            hop = hops[i]
            if not _is_valid_ip(hop):
                break
            if not _is_ip_in_networks(hop, trusted_proxies):
                return hop
        else:
            if hops and _is_valid_ip(hops[0]):
                return hops[0]

    return peer


def _serialize(obj):
    """Recursively convert Pydantic models to dicts so JSONResponse can serialize them."""
    if isinstance(obj, BaseModel):
        return _serialize(obj.model_dump())
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    return obj


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# REQUEST PROCESSING PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def _sse_response(request: Request, generator: Callable, auth_validator: Optional[Callable]):
    """Shared SSE request-handling body used by add_sse_endpoint's two wrapper variants."""
    from fastapi.responses import StreamingResponse

    data: dict = {}
    data.update(request.query_params)
    data.update(request.path_params)

    if auth_validator:
        token = _extract_bearer_token(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "Missing authorization token"},
            )
        if inspect.iscoroutinefunction(auth_validator):
            payload = await auth_validator(token)
        else:
            payload = await run_in_threadpool(auth_validator, token)
        if not payload:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "Invalid or expired token"},
            )
        data["_auth"] = payload

    async def event_stream():
        gen = generator(data)
        try:
            async for chunk in gen:
                if await request.is_disconnected():
                    break
                yield chunk
        finally:
            await gen.aclose()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _process_request(
    request: Request,
    body_data: Any,
    handler: Callable,
    auth_validator: Optional[Callable],
    paused_owners: set,
    files: Optional[list] = None,
    trusted_proxies: Optional[Sequence[Any]] = None,
    custom_ip_header: Optional[str] = None,
    trust_cloudflare: bool = False,
) -> Any:
    """
    Core request processing pipeline. Executed for every incoming HTTP request.

    Phases:
        1. Data Assembly   — merge path params + query params + body into one flat dict
        2. Context Seeding — set causality ContextVars (event_id, identity)
        3. Authentication  — validate token if auth_validator is provided → inject into data["_auth"]
        4. Dispatch        — call the plugin handler (async or sync)
        5. Response        — serialize result as JSONResponse with the correct status code
    """
    # ── Phase 1: Data Assembly ─────────────────────────────────────────────
    # Keep the exact inbound bytes available to plugins that need cryptographic
    # verification (payment webhooks). Starlette caches the body in ordinary
    # JSON requests. If an upstream multipart/form parser already consumed the
    # stream, do not fail the request: file endpoints do not use raw_body.
    try:
        raw_body = await request.body()
    except RuntimeError:
        raw_body = b""
    data: dict = {}
    # 1. Query parameters always come from the request object
    data.update(request.query_params)

    # 2. Path parameters always included
    data.update(request.path_params)

    # 3. Body/Form data
    # If body_data is provided (from FastAPI DI), it contains body/form fields
    if body_data is not None:
        if hasattr(body_data, "model_dump"):
            data.update(body_data.model_dump())
        elif hasattr(body_data, "dict"):
            data.update(body_data.dict())
        elif isinstance(body_data, dict):
            data.update(body_data)
    else:
        # Fallback: manual extraction if no DI model was used
        content_type = request.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                raw_json = await request.json()
                if isinstance(raw_json, dict):
                    data.update(raw_json)
            except Exception: pass
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            try:
                form = await request.form()
                for key, value in form.items():
                    if not hasattr(value, "filename"): # Only take non-field data
                        data[key] = value
            except Exception: pass

    if files is not None:
        # Wrapped at the boundary: a plugin must never hold the web
        # framework's own upload object (see types.py).
        data["_files"] = [UploadedFile.from_starlette(f) for f in files]

    # ── Phase 2: Causality Context Seeding ────────────────────────────────
    # Honor X-Request-ID from an upstream MicroCoreOS service if present,
    # so the entire cross-service call chain shares the same root event ID.
    # If absent (first hop or external client), generate a fresh UUID.
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    # Same identity scheme as the event bus: prefer the kernel-stamped
    # "domain.ClassName" so emitters and subscribers share one format.
    owner = getattr(handler, "__self__", None)
    if owner is not None:
        base = getattr(owner, "_identity", None) or owner.__class__.__name__
        identity = f"{base}.{handler.__name__}"
    else:
        identity = getattr(handler, "__name__", "unknown")
    id_token = current_event_id_var.set(request_id)
    ident_token = current_identity_var.set(identity)
    print(
        f"[HttpServer] → {request.method} {request.url.path}"
        f"  req={request_id[:8]}  identity={identity}"
    )

    try:
        # Chaos/ops pause (Issue 34): the paused owner's endpoints answer
        # 503 before auth or dispatch — simulates the service being down.
        if any(identity == p or identity.startswith(p + ".")
               for p in paused_owners):
            return JSONResponse(
                status_code=503,
                content={"success": False, "error": "Service temporarily unavailable (paused)"},
            )

        context = HttpContext(
            client_ip=_extract_client_ip(
                request,
                trusted_proxies=trusted_proxies,
                custom_ip_header=custom_ip_header,
                trust_cloudflare=trust_cloudflare,
            ),
            request_headers=request.headers,
            raw_body=raw_body,
        )

        # ── Phase 3: Authentication ────────────────────────────────────────
        if auth_validator:
            token = _extract_bearer_token(request)
            if not token:
                return JSONResponse(
                    status_code=401,
                    content={"success": False, "error": "Missing authorization token"},
                )
            if inspect.iscoroutinefunction(auth_validator):
                payload = await auth_validator(token)
            else:
                payload = await run_in_threadpool(auth_validator, token)

            if not payload:
                return JSONResponse(
                    status_code=401,
                    content={"success": False, "error": "Invalid or expired token"},
                )
            data["_auth"] = payload

        # ── Phase 4: Handler Dispatch ──────────────────────────────────────
        if inspect.iscoroutinefunction(handler):
            result = await handler(data, context)
        else:
            result = await run_in_threadpool(handler, data, context)

        status_code = context.status_code
        if not context._status_explicit and isinstance(result, dict) and result.get("success") is False:
            status_code = 400

        print(
            f"[HttpServer] ← {request.method} {request.url.path}"
            f"  req={request_id[:8]}  status={status_code}"
        )

        # ── Phase 5: Response ──────────────────────────────────────────────
        if context.redirect_url:
            from fastapi.responses import RedirectResponse
            redirect_response = RedirectResponse(
                url=context.redirect_url, status_code=context.status_code
            )
            for key, value in context._headers.items():
                redirect_response.headers[key] = value
            for cookie in context._cookies:
                redirect_response.set_cookie(**cookie)
            return redirect_response

        binary = context.binary_content
        if binary:
            from fastapi.responses import Response
            content, media_type = binary
            response = Response(content=content, media_type=media_type, status_code=context.status_code)
            context.apply_to(response)
            return response

        json_response = JSONResponse(status_code=status_code, content=_serialize(result))
        context.apply_to(json_response)
        return json_response

    except Exception as e:
        # Unhandled exception: log the real error server-side, return generic message to client.
        print(f"[HttpServer] 💥 Unhandled exception in '{identity}': {e}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "Internal server error"},
        )
    finally:
        current_identity_var.reset(ident_token)
        current_event_id_var.reset(id_token)


# ── Utilities ────────────────────────────────────────────────────────────────

def _extract_ws_token(websocket) -> Optional[str]:
    """
    Token for a WebSocket handshake: Authorization header, then the `token`
    query parameter, then the access_token cookie.

    The query parameter exists because browsers cannot set headers on a
    WebSocket handshake. It is also the least private of the three — query
    strings reach access logs and proxies — so it is tried after the header.

    No CSRF guard applies here the way it does for cookie-authenticated
    mutations: a WebSocket handshake is not a form submission, and its
    cross-origin requests are governed by the Origin check, not by headers a
    form could forge.
    """
    auth_header = websocket.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    token = websocket.query_params.get("token")
    if token:
        return token

    return websocket.cookies.get("access_token")


def _extract_bearer_token(request: Request) -> Optional[str]:
    """
    Extracts the Bearer token from the request.
    Priority:
      1. Authorization header (Bearer) -> Preferred for Apps/CLI, immune to CSRF.
      2. access_token cookie -> Subject to CSRF, requires X-Requested-With guard.
    """
    # 1. Bearer Token (Highest security, default for non-browser clients)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # 2. Cookie Auth (Web clients)
    token = request.cookies.get("access_token")
    if token:
        # CSRF Guard: If it's a mutation method (POST/PUT/DELETE) and we are
        # using cookies, we MUST verify the request was initiated by our own
        # JavaScript. An attacker-controlled form cannot add custom headers.
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            if not request.headers.get("X-Requested-With"):
                print(f"[HttpServer] 🛡️ CSRF block: Mutation {request.method} "
                      f"via cookie missing X-Requested-With header.")
                return None
        return token

    return None

"""
HTTP Server Tool — Reference Implementation for MicroCoreOS
============================================================

This is the REFERENCE IMPLEMENTATION for HTTP server tools in MicroCoreOS.
Any new HTTP tool (aiohttp, Hypercorn + Quart, etc.) MUST follow this contract.

PUBLIC CONTRACT (what plugins use):
────────────────────────────────────────────────────────────────────────────────

    # Register a REST endpoint
    http.add_endpoint(
        path="/users/{user_id}",          # FastAPI path format for path parameters
        method="GET",                      # HTTP method (case-insensitive)
        handler=self.execute,             # async or sync callable
        tags=["Users"],                    # Optional: OpenAPI grouping
        request_model=UserEntity,         # Optional: Pydantic model → body validation + schema
        response_model=UserResponse,      # Optional: Pydantic model → OpenAPI response schema
        auth_validator=self._validate,    # Optional: token validator (see AUTH section)
        has_files=False,                  # Optional: if True, enables multipart/form-data
    )

    # Serve static files from a directory. Only DEFAULT_STATIC_EXTENSIONS are
    # served; pass allow_extensions to narrow or widen that set.
    http.mount_static("/static", "./public")

    # Serve a UI: html=True resolves directory requests to their index.html.
    # Mounts are applied after every endpoint, so "/" does not shadow the API.
    http.mount_static("/", "./ui", html=True)

    # WebSocket endpoint
    http.add_ws_endpoint(
        path="/ws/chat",
        on_connect=self.on_ws_connect,     # receives a WebSocketConnection (types.py)
        on_disconnect=self.on_ws_disconnect,  # optional, called on disconnect
        auth_validator=self._validate,     # optional; rejects BEFORE the handshake.
                                           # on_connect then takes (conn, auth_payload)
    )

    # Server-Sent Events endpoint
    http.add_sse_endpoint(
        path="/events/stream",
        generator=self._stream,            # async generator: yields "data: ...\n\n" strings
        tags=["Events"],
        auth_validator=self._validate,     # optional, same contract as add_endpoint
    )


HANDLER SIGNATURE:
────────────────────────────────────────────────────────────────────────────────

    async def execute(self, data: dict, context: HttpContext) -> dict:
        # 'data' is a flat dict merging: path params + query params + body
        # If has_files=True, 'data["_files"]' holds UploadedFile objects (types.py):
        #   .filename  .content_type  .stream (sync, for storage clients)  await .read()
        # 'context' is an HttpContext handle for response manipulation
        return {"success": True, "data": {...}}


RESPONSE CONTRACT:
────────────────────────────────────────────────────────────────────────────────

    # Success (HTTP 200 by default)
    return {"success": True, "data": {...}}

    # Business error (HTTP 400 by default — set_status() was never called)
    return {"success": False, "error": "User not found"}

    # More specific status: set_status() always wins over the default
    context.set_status(404)
    return {"success": False, "error": "User not found"}

    # Binary response (e.g. images, PDFs)
    context.set_binary_response(b"...", media_type="image/png")
    return {} # handler return value is ignored when binary response is set

    # Auth failure — handled automatically (HTTP 401, envelope format)
    # {"success": False, "error": "Missing authorization token"}
    # {"success": False, "error": "Invalid or expired token"}

    # Validation failure — handled automatically (HTTP 422, envelope format)
    # {"success": False, "error": "<first validation message>", "details": [...]}

    # Unhandled exception — caught by the tool (HTTP 500, envelope format)
    # {"success": False, "error": "Internal server error"}
    # (exception details are logged server-side, NOT exposed to clients)


HttpContext API:
────────────────────────────────────────────────────────────────────────────────

    context.set_status(code: int)           → Override HTTP status code
                                               (default: 200 on success, 400 on
                                               success:false unless overridden)
    context.set_cookie(key, value, ...)     → Set a response cookie
    context.set_header(key, value)          → Add a custom response header
    context.redirect(url, status=302)       → Redirect to another URL
    context.set_binary_response(content, media_type) → Return raw binary data
    context.client_ip                       → Best-effort caller IP (property, not a
                                               call) — a raw signal for the PLUGIN's own
                                               policy (identity-aware rate limiting,
                                               audit logging...). See INSTRUCTIONS_FOR_AI.md's
                                               Rate Limiting Pattern: this tool never
                                               decides a policy, it only exposes the signal.


AUTH VALIDATOR CONTRACT:
────────────────────────────────────────────────────────────────────────────────

    async def _validate_token(self, token: str) -> dict | None:
        try:
            return self.auth.decode_token(token)   # Return payload dict on success
        except Exception:
            return None                            # Return None to trigger HTTP 401

    # The returned payload is injected into data["_auth"] for the handler to use.
    # The token is extracted from: Authorization: Bearer <token>  OR  Cookie: access_token=<token>

    # WHY THIS IS A CALLBACK. This tool must never import an auth tool: they are
    # swapped separately, and one of them is not even installed by default
    # (`microcoreos add auth`). A plugin receives both by injection and wires
    # them together itself — composition lives in the plugin layer, never
    # between tools. That decoupling is what let auth move out of the default
    # project without one line changing here.


REPLACEMENT STANDARD (implement this to swap the backend):
────────────────────────────────────────────────────────────────────────────────

    To create an aiohttp-based implementation:

    1. Create tools/aiohttp_server/aiohttp_server_tool.py  lint:no-path
    2. name = "http"                               ← same injection key, plugins are unaffected
    3. Implement the public methods:
          add_endpoint(path, method, handler, tags, request_model, response_model, auth_validator, has_files)
          mount_static(path, directory_path, html=False, allow_extensions=None)
          add_ws_endpoint(path, on_connect, on_disconnect, auth_validator)
          add_sse_endpoint(path, generator, tags, auth_validator)
    4. Handler contract: handler(data: dict, context: HttpContext) → dict
       - data: flat merge of path params + query params + body (+ _files if applicable)
       - context: instance of HttpContext (or a compatible duck-type), constructed with
         client_ip= the best-effort caller IP for this backend's own request object
         (see pipeline.py's _extract_client_ip for the header trust order to mirror)
    5. Honor context.status_code and context.binary_content for the HTTP response.
       If the handler never called context.set_status() and the result dict has
       success: False, default to HTTP 400 instead of 200 (context._status_explicit
       tracks whether set_status() was called).
    6. For auth: call auth_validator(token), inject payload into data["_auth"]
    7. On auth failure: return HTTP 401 with {"success": False, "error": "..."}
    8. On unhandled exception: return HTTP 500 with {"success": False, "error": "Internal server error"}

    Plugins will NOT require any changes.
"""

import os
import stat
import asyncio
import inspect
import uvicorn
from typing import Optional, Callable
from fastapi.exceptions import RequestValidationError
from microcoreos import BaseTool
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, File, UploadFile, Security
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

# HttpContext and the request-processing pipeline were split out into their
# own modules (mechanical move, no behavior change). Re-exported here since
# external code imports HttpContext from this module.
from tools.http_server.context import HttpContext  # noqa: F401 — re-export
from tools.http_server.pipeline import (
    _process_request,
    _sse_response,
    _extract_ws_token,
    _parse_trusted_proxies,
    _parse_ws_origins,
    _validate_ws_origin,
)
from tools.http_server.types import UploadedFile, WebSocketConnection  # noqa: F401 — re-export

# Extensions mount_static() serves when the caller declares none.
DEFAULT_STATIC_EXTENSIONS = frozenset({
    "html", "htm", "css", "js", "mjs", "json", "txt", "xml", "webmanifest",
    "svg", "png", "jpg", "jpeg", "gif", "webp", "avif", "ico",
    "woff", "woff2", "ttf", "otf",
    "mp4", "webm", "mp3", "ogg", "wasm", "pdf",
})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP SERVER TOOL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HttpServerTool(BaseTool):

    def __init__(self):
        self.app = FastAPI(title="MicroCoreOS Gateway")
        self._port: int = int(os.getenv("HTTP_PORT", 5000))
        self._server: Optional[uvicorn.Server] = None
        self._pending_endpoints: list[dict] = []
        self._pending_mounts: list[dict] = []
        self._pre_mount_hooks: list[Callable] = []
        # Documentation-only security scheme: shows the "Authorize" button in
        # Swagger UI (/docs) and marks protected routes with a lock icon.
        # auto_error=False is critical — actual token validation still happens
        # in _process_request via auth_validator; this dependency must never
        # short-circuit with its own 401/403 before that runs.
        self._bearer_scheme = HTTPBearer(
            auto_error=False,
            description="Paste the token as-is (JWT, no 'Bearer ' prefix — Swagger adds it).",
        )
        # Chaos/ops pause (Issue 34): owner identities ("domain.Class", or a
        # bare domain prefix) whose endpoints answer 503 without dispatching
        # (routes stay mounted — the "service" is simply down for callers).
        # Mirror of the event bus's set; mutated only by the chaos extras
        # plugin via its sanctioned raw-tool introspection.
        self._paused_owners: set[str] = set()

        # Trusted reverse proxy and client IP resolution
        self._trusted_proxies: list = []
        self._custom_ip_header: Optional[str] = None
        self._trust_cloudflare: bool = False

        # WebSocket origin policy
        self._ws_origin_policy: str = "off"
        self._ws_allowed_origins: set[str] = set()
        self._ws_allow_missing_origin: bool = False

        self._load_config_from_env()

    def _load_config_from_env(self) -> None:
        """Load and normalize proxy and WebSocket configuration from environment variables."""
        trusted_proxies_raw = os.getenv("HTTP_TRUSTED_PROXIES", "").strip()
        try:
            self._trusted_proxies = _parse_trusted_proxies(trusted_proxies_raw)
        except ValueError:
            self._trusted_proxies = []

        custom_header = os.getenv("HTTP_CUSTOM_CLIENT_IP_HEADER", "").strip()
        cf_raw = os.getenv("HTTP_TRUST_CLOUDFLARE", "false").strip().lower()
        self._custom_ip_header = custom_header or None
        self._trust_cloudflare = cf_raw in ("true", "1")

        policy_raw = os.getenv("HTTP_WS_ORIGIN_POLICY", "off").strip().lower()
        self._ws_origin_policy = policy_raw

        origins_raw = os.getenv("HTTP_WS_ORIGINS", "").strip()
        try:
            self._ws_allowed_origins = _parse_ws_origins(origins_raw) if origins_raw else set()
        except ValueError:
            self._ws_allowed_origins = set()

        missing_raw = os.getenv("HTTP_WS_ALLOW_MISSING_ORIGIN", "false").strip().lower()
        self._ws_allow_missing_origin = missing_raw in ("true", "1")

    @property
    def name(self) -> str:
        return "http"

    # ── Lifecycle ────────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        host = os.getenv("HTTP_HOST", "127.0.0.1")
        cors_origins_raw = os.getenv("HTTP_CORS_ORIGINS", "*")
        
        if host == "0.0.0.0" and cors_origins_raw == "*":
            print("[HttpServer] ⚠️  SECURITY WARNING: Server is exposed to 0.0.0.0 with CORS '*'. "
                  "This is insecure for production.")

        # Fail-fast for weak auth key if auth is present
        secret = os.getenv("AUTH_SECRET_KEY", "")
        if secret and len(secret) < 32:
            print("[HttpServer] ⚠️  SECURITY WARNING: AUTH_SECRET_KEY is too short (< 32 chars).")

        print(f"[HttpServer] Configuring FastAPI on port {self._port}...")

        @self.app.exception_handler(RequestValidationError)
        async def validation_error_handler(request: Request, exc: RequestValidationError):
            first_error = exc.errors()[0] if exc.errors() else {}
            message = first_error.get("msg", "Validation error")
            return JSONResponse(
                status_code=422,
                content={
                    "success": False,
                    "error": message,
                    "details": exc.errors(),
                },
            )

        @self.app.middleware("http")
        async def add_security_headers(request: Request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            return response

        cors_origins = [o.strip() for o in cors_origins_raw.split(",")] if cors_origins_raw != "*" else ["*"]
        # Cookie auth from a different origin needs this on; it is off by
        # default because turning it on makes every cross-origin request
        # carry the session cookie.
        allow_credentials = os.getenv("HTTP_CORS_CREDENTIALS", "false").strip().lower() == "true"

        if allow_credentials and "*" in cors_origins:
            # Starlette answers this pair by echoing the caller's Origin back,
            # so every site could send the session cookie — and the preflight
            # would approve the X-Requested-With that the CSRF guard needs.
            raise ValueError(
                "HTTP_CORS_CREDENTIALS=true requires an explicit HTTP_CORS_ORIGINS list. "
                "With '*' every origin would be allowed to send the session cookie."
            )

        # Validate trusted reverse proxies configuration
        trusted_raw = os.getenv("HTTP_TRUSTED_PROXIES", "").strip()
        self._trusted_proxies = _parse_trusted_proxies(trusted_raw) if trusted_raw else []

        if host == "0.0.0.0" and trusted_raw == "*":
            print("[HttpServer] ⚠️  SECURITY WARNING: Server is exposed to 0.0.0.0 with HTTP_TRUSTED_PROXIES '*'. "
                  "Set explicit proxy IPs/CIDRs for production.")

        custom_header = os.getenv("HTTP_CUSTOM_CLIENT_IP_HEADER", "").strip()
        self._custom_ip_header = custom_header or None

        cf_raw = os.getenv("HTTP_TRUST_CLOUDFLARE", "false").strip().lower()
        if cf_raw not in ("true", "false", "1", "0", ""):
            raise ValueError(f"Invalid HTTP_TRUST_CLOUDFLARE: {cf_raw!r}. Must be 'true' or 'false'.")
        self._trust_cloudflare = cf_raw in ("true", "1")

        # Validate WebSocket Origin policy configuration
        ws_policy = os.getenv("HTTP_WS_ORIGIN_POLICY", "off").strip().lower()
        if ws_policy not in ("off", "allowlist"):
            raise ValueError(
                f"Invalid HTTP_WS_ORIGIN_POLICY: {ws_policy!r}. Must be 'off' or 'allowlist'."
            )
        self._ws_origin_policy = ws_policy

        if self._ws_origin_policy == "allowlist":
            ws_origins_raw = os.getenv("HTTP_WS_ORIGINS", "").strip()
            if not ws_origins_raw:
                raise ValueError(
                    "HTTP_WS_ORIGINS must not be empty when HTTP_WS_ORIGIN_POLICY='allowlist'"
                )
            self._ws_allowed_origins = _parse_ws_origins(ws_origins_raw)
            if not self._ws_allowed_origins:
                raise ValueError(
                    "HTTP_WS_ORIGINS must contain at least one valid origin when HTTP_WS_ORIGIN_POLICY='allowlist'"
                )
        else:
            ws_origins_raw = os.getenv("HTTP_WS_ORIGINS", "").strip()
            self._ws_allowed_origins = _parse_ws_origins(ws_origins_raw) if ws_origins_raw else set()

        ws_missing_raw = os.getenv("HTTP_WS_ALLOW_MISSING_ORIGIN", "false").strip().lower()
        if ws_missing_raw not in ("true", "false", "1", "0", ""):
            raise ValueError(
                f"Invalid HTTP_WS_ALLOW_MISSING_ORIGIN: {ws_missing_raw!r}. Must be 'true' or 'false'."
            )
        self._ws_allow_missing_origin = ws_missing_raw in ("true", "1")

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )


    async def on_boot_complete(self, container) -> None:
        """
        Registers all buffered endpoints and starts the uvicorn server.
        Endpoints are buffered (not registered immediately in add_endpoint) to allow
        FastAPI to sort static paths before parameterized paths, preventing routing conflicts.
        """
        self._run_pre_mount_hooks()
        self._register_all_endpoints()
        self._register_all_static_mounts()
        host = os.getenv("HTTP_HOST", "127.0.0.1")
        log_level = os.getenv("HTTP_LOG_LEVEL", "warning")
        config = uvicorn.Config(
            self.app,
            host=host,
            port=self._port,
            log_level=log_level,
            proxy_headers=False,
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())
        print(f"[HttpServer] Server active → http://localhost:{self._port}/docs")

    async def on_instrument(self, tracer_provider) -> None:
        """Driver-level OTel instrumentation for FastAPI.
        Adds HTTP span attributes: method, route, status code, latency.
        Called by TelemetryTool after boot, bypassing ToolProxy.
        """
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(self.app)
            print("[HttpServerTool] FastAPI instrumented for OTel.")
        except ImportError:
            print("[HttpServerTool] opentelemetry-instrumentation-fastapi not installed — "
                  "HTTP driver spans unavailable. ToolProxy spans still active.")

    async def shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True
            if self._server_task:
                try:
                    await asyncio.wait_for(self._server_task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass

    def get_interface_description(self) -> str:
        return """
        HTTP Server Tool (http):
        - PURPOSE: FastAPI-powered HTTP gateway. Supports REST, static files, WebSockets and SSE.
        - HANDLER SIGNATURE: async def execute(self, data: dict, context: HttpContext) -> dict
          'data' = flat merge of [path params] + [query params] + [body/form fields].
          Special keys in 'data':
            - data["_auth"]: contains the payload from auth_validator if successful.
            - data["_files"]: list of UploadedFile objects (only if has_files=True).
                Fields: .filename, .content_type, .stream (sync file object), await .read().
        - SECURITY DEFAULTS:
            - Cookies set via context.set_cookie are 'Secure=True', 'HttpOnly=True', 'SameSite=Lax'.
            - CSRF Guard: Mutations (POST/PUT/DELETE) using cookie auth REQUIRE 'X-Requested-With' header.
            - WebSocket Origin Policy: Configurable via HTTP_WS_ORIGIN_POLICY=off|allowlist.
              In allowlist mode, connections from unlisted origins are closed with 1008 before handshake.
            - Trusted Proxies: Forwarding headers (X-Forwarded-For or custom edge header)
              are only evaluated if the direct peer is in HTTP_TRUSTED_PROXIES. Otherwise direct peer IP
              is used. An optional custom edge header is configurable via HTTP_CUSTOM_CLIENT_IP_HEADER.
            - Swagger UI (/docs): endpoints with auth_validator show a lock icon and accept
              tokens via the "Authorize" button (documentation-only; real check unaffected).
        - CAPABILITIES:
            - add_endpoint(path, method, handler, tags=None, request_model=None,
                           response_model=None, auth_validator=None, has_files=False):
                - has_files: if True, enables multipart/form-data. Request model fields 
                  become Form fields. To use a file: file = data["_files"][0]; 
                  await s3.upload_fileobj(file.filename, file.file, content_type=file.content_type)
            - mount_static(path, directory_path, html=False, allow_extensions=None):
                Serve static files from a directory. Deny by default: only files whose
                extension is allowed are served (default DEFAULT_STATIC_EXTENSIONS; pass
                a set to declare your own, or "*" to serve everything). Dotfiles are
                always refused except under '.well-known/'. Use html=True to serve
                index.html for directory requests, which a UI/SPA mounted at "/" needs.
                Raises ValueError if the directory does not exist.
            - add_ws_endpoint(path, on_connect, on_disconnect=None, auth_validator=None):
                WebSocket support. on_connect receives a WebSocketConnection: send_text,
                send_json, receive_text, receive_json, close, query_params, path_params.
                WebSocket Origin policy (HTTP_WS_ORIGIN_POLICY) is enforced first.
                With auth_validator the token is read from the Authorization header, the
                `token` query param, then the access_token cookie; an invalid one is
                closed with 1008 BEFORE the handshake and on_connect takes (conn, payload).
            - add_sse_endpoint(path, generator, tags=None, auth_validator=None):
                Server-Sent Events. generator yields formatted strings: "data: {...}\\n\\n".
            - register_pre_mount_hook(hook): hook(endpoints: list[dict]) is called once in
                on_boot_complete(), before routes are mounted, with every buffered endpoint
                (method, path, owner) — the first point where all plugins' add_endpoint()
                calls are guaranteed to have run. Used for boot-time checks across ALL
                registered routes (e.g. the architecture linter's route-collision scan).
        - HttpContext CAPABILITIES (inside handler):
            - context.set_status(code: int): Override HTTP status. Default is 200 on
              success:true; 400 on success:false unless set_status() is called.
            - context.redirect(url: str, status=302): Redirect to another URL.
            - context.set_cookie(key, value, max_age=3600, ...): Set secure response cookie.
            - context.set_header(key, value): Add custom response header.
            - context.set_binary_response(content: bytes, media_type: str): Return raw file.
            - context.raw_body: Exact inbound HTTP request body bytes. Use for webhook
              signature verification; providers sign bytes, not a re-serialized dict.
            - context.get_header(key, default=None): Read inbound request headers
              case-insensitively (e.g. X-Signature for signed webhooks).
            - context.client_ip: Best-effort caller IP (property). Resolved against
              HTTP_TRUSTED_PROXIES (no trust by default). Direct callers cannot spoof
              forwarding headers. When proxied, right-to-left X-Forwarded-For evaluation
              determines the first untrusted caller. Custom edge header configurable via
              HTTP_CUSTOM_CLIENT_IP_HEADER. Never security-authoritative on its own.
        - RESPONSE CONTRACT:
            - Standard: return {"success": bool, "data": ..., "error": ...}
            - WARNING: All values in 'data' must be JSON-serializable. Pydantic model 
              instances are NOT serializable — always call .model_dump() before returning.
        """

    # ── Public API ───────────────────────────────────────────────────────────────

    def add_endpoint(
        self,
        path: str,
        method: str,
        handler: Callable,
        tags: Optional[list] = None,
        request_model=None,
        response_model=None,
        auth_validator: Optional[Callable] = None,
        has_files: bool = False,
    ) -> None:
        """
        Registers an HTTP endpoint. Buffered until on_boot_complete() to allow
        correct path ordering (static routes before parameterized ones).
        """
        self._pending_endpoints.append({
            "path": path,
            "method": method,
            "handler": handler,
            "tags": tags,
            "request_model": request_model,
            "response_model": response_model,
            "auth_validator": auth_validator,
            "has_files": has_files,
        })

    def register_pre_mount_hook(self, hook: Callable[[list[dict]], None]) -> None:
        """
        Registers a callback invoked once, in on_boot_complete(), with the full
        list of buffered endpoints — the first point where every plugin's
        add_endpoint() calls are guaranteed to have run (add_endpoint only
        buffers; per-plugin on_boot() calls race each other, but they all
        finish before any tool's on_boot_complete does).
        Each endpoint dict has: method, path, owner (the registering plugin's
        identity, or its handler's class name as a fallback).
        Used by cross-cutting boot-time checks (e.g. the architecture linter's
        route-collision scan) that need visibility into ALL endpoints, not
        just the ones the calling plugin registered itself.
        """
        self._pre_mount_hooks.append(hook)

    def _run_pre_mount_hooks(self) -> None:
        if not self._pre_mount_hooks:
            return
        endpoints = []
        for ep in self._pending_endpoints:
            owner_obj = getattr(ep["handler"], "__self__", None)
            owner = getattr(owner_obj, "_identity", None) or (
                owner_obj.__class__.__name__ if owner_obj is not None else repr(ep["handler"])
            )
            endpoints.append({"method": ep["method"], "path": ep["path"], "owner": owner})
        for hook in self._pre_mount_hooks:
            hook(endpoints)

    def mount_static(
        self,
        path: str,
        directory_path: str,
        html: bool = False,
        allow_extensions: Optional[set] = None,
        optional: bool = False,
    ) -> None:
        """Serves static files from a local directory. Deny by default.

        Only files whose extension is in allow_extensions are reachable;
        everything else 404s. Dot-prefixed segments ('.env', '.git/config') are
        always refused, except under '.well-known/'.

            allow_extensions=None                   # DEFAULT_STATIC_EXTENSIONS
            allow_extensions={"html", "css", "js"}  # exactly these
            allow_extensions="*"                    # serve everything

        html=True serves 'index.html' for directory requests, which is what a
        UI or SPA mounted at "/" needs. Off by default: it also makes a miss
        render '404.html' when that file exists.

        optional=True skips the mount gracefully with a warning if directory_path
        does not exist yet (e.g. unbuilt frontend in dev), instead of raising ValueError.

        The mount is buffered and applied in on_boot_complete(), after every
        endpoint. Starlette matches routes in registration order and a Mount
        matches its whole subtree, so mounting "/" earlier would shadow the API.

        Raises ValueError if the directory does not exist and optional is False.
        """
        if not os.path.isdir(directory_path):
            if optional:
                print(
                    f"[HttpServer] ⚠️  mount_static: optional directory not found: {directory_path!r} "
                    f"(skipping mount)"
                )
                return
            raise ValueError(
                f"mount_static: directory not found: {directory_path!r} "
                f"(resolved to {os.path.abspath(directory_path)!r})"
            )


        if allow_extensions is None:
            allow_extensions = DEFAULT_STATIC_EXTENSIONS
        elif allow_extensions != "*":
            allow_extensions = frozenset(e.lstrip(".").lower() for e in allow_extensions)

        self._pending_mounts.append({
            "path": path,
            "directory_path": directory_path,
            "html": html,
            "allow_extensions": allow_extensions,
        })

    class _AllowlistedStaticFiles(StaticFiles):
        """
        StaticFiles restricted to declared extensions. Plain StaticFiles serves
        everything under the directory; this serves only what was allowed.

        Blocked files return "no result" rather than raising, so they 404
        exactly like missing ones and existence is never confirmed.

        '.well-known/' bypasses both checks: it is public by spec and its ACME
        challenge tokens have no extension.
        """

        _WELL_KNOWN = ".well-known"

        def __init__(self, *args, allow_extensions, **kwargs):
            super().__init__(*args, **kwargs)
            self.allow_extensions = allow_extensions

        def lookup_path(self, path: str):
            # get_path() normalizes the mount root to "." and strips traversal,
            # so "" and "." are structural rather than dot-prefixed names.
            segments = [s for s in path.split(os.sep) if s not in ("", ".")]
            exempt = bool(segments) and segments[0] == self._WELL_KNOWN

            if not exempt and any(s.startswith(".") for s in segments):
                return "", None

            full_path, stat_result = super().lookup_path(path)

            # Directories pass through unchecked: html mode resolves them by
            # re-entering here with "<dir>/index.html", which is checked.
            if stat_result is None or stat.S_ISDIR(stat_result.st_mode):
                return full_path, stat_result

            if exempt or self.allow_extensions == "*":
                return full_path, stat_result

            ext = os.path.splitext(segments[-1])[1].lstrip(".").lower()
            if ext not in self.allow_extensions:
                return "", None

            return full_path, stat_result

    def _register_all_static_mounts(self) -> None:
        """
        Applies buffered static mounts. Must run after _register_all_endpoints():
        a Mount swallows every route registered after it within its subtree.
        """
        for m in self._pending_mounts:
            self.app.mount(
                m["path"],
                self._AllowlistedStaticFiles(
                    directory=m["directory_path"],
                    html=m["html"],
                    allow_extensions=m["allow_extensions"],
                ),
                name=m["path"],
            )
            print(f"[HttpServer] Static mount — {m['path']!r} → {m['directory_path']!r} (html={m['html']})")

    def add_ws_endpoint(
        self,
        path: str,
        on_connect: Callable,
        on_disconnect: Optional[Callable] = None,
        auth_validator: Optional[Callable] = None,
    ) -> None:
        """
        Registers a WebSocket endpoint.

        auth_validator has the same contract as add_endpoint's: it receives the
        token and returns a payload, or a falsy value to reject. It runs BEFORE
        the handshake is accepted — a rejected connection is closed with 1008
        and on_connect never sees it.

        Browsers cannot set headers on a WebSocket handshake, so the token is
        read from the Authorization header, the `token` query parameter, then
        the access_token cookie, in that order. The query parameter is there
        because browsers have no other option; it lands in access logs and
        proxy logs, so prefer short-lived tokens for it.

        on_connect receives a WebSocketConnection (see types.py) plus, when
        auth_validator is set, the payload it returned.
        """
        @self.app.websocket(path)
        async def ws_handler(websocket: WebSocket):
            # 1. WebSocket Origin Policy check (runs before auth and accept)
            is_allowed, reason = _validate_ws_origin(
                websocket,
                policy=self._ws_origin_policy,
                allowed_origins=self._ws_allowed_origins,
                allow_missing=self._ws_allow_missing_origin,
            )
            if not is_allowed:
                print(f"[HttpServer] 🛡️ WebSocket rejected on {path}: {reason}")
                await websocket.close(code=1008)
                return

            auth_payload = None
            if auth_validator:
                token = _extract_ws_token(websocket)
                if token:
                    if inspect.iscoroutinefunction(auth_validator):
                        auth_payload = await auth_validator(token)
                    else:
                        auth_payload = await run_in_threadpool(auth_validator, token)
                if not auth_payload:
                    # Rejected before accept(): no handshake, nothing to close
                    # gracefully, and on_connect is never reached.
                    await websocket.close(code=1008)
                    print(f"[HttpServer] 🛡️ WebSocket rejected on {path}: invalid or missing token")
                    return

            await websocket.accept()
            conn = WebSocketConnection(websocket)
            args = (conn, auth_payload) if auth_validator else (conn,)
            try:
                if inspect.iscoroutinefunction(on_connect):
                    await on_connect(*args)
                else:
                    await run_in_threadpool(on_connect, *args)
            except WebSocketDisconnect:
                if on_disconnect:
                    if inspect.iscoroutinefunction(on_disconnect):
                        await on_disconnect(conn)
                    else:
                        await run_in_threadpool(on_disconnect, conn)
            except Exception as e:
                print(f"[HttpServer] WebSocket error on {path}: {e}")
                if on_disconnect:
                    try:
                        if inspect.iscoroutinefunction(on_disconnect):
                            await on_disconnect(conn)
                        else:
                            await run_in_threadpool(on_disconnect, conn)
                    except Exception:
                        pass

    def add_sse_endpoint(
        self,
        path: str,
        generator: Callable,
        tags: Optional[list] = None,
        auth_validator: Optional[Callable] = None,
    ) -> None:
        """
        Registers a Server-Sent Events endpoint (GET, text/event-stream).

        generator: async generator callable(data: dict) that yields pre-formatted SSE strings,
                   e.g. "data: {...}\\n\\n". The generator's finally block runs on client disconnect.
        """
        if auth_validator:
            # Documentation-only Bearer dependency (see add_endpoint) so Swagger
            # UI marks this SSE route as protected. Real auth check is below.
            async def sse_handler(
                request: Request,
                _bearer_auth: Optional[HTTPAuthorizationCredentials] = Security(self._bearer_scheme),
            ):
                return await _sse_response(request, generator, auth_validator)
        else:
            async def sse_handler(request: Request):
                return await _sse_response(request, generator, auth_validator)

        clean_path = path.replace("/", "_")
        sse_handler.__name__ = f"sse{clean_path}"
        # Declare the real media type in OpenAPI: without response_class +
        # responses, FastAPI would advertise application/json for a route that
        # actually emits text/event-stream, and contract-driven consumers
        # (probes, generated clients) would treat it as a normal JSON endpoint.
        from fastapi.responses import StreamingResponse
        self.app.add_api_route(
            path, sse_handler, methods=["GET"], tags=tags or [],
            response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {}},
                             "description": "Server-Sent Events stream"}},
        )

    # ── Endpoint registration ────────────────────────────────────────────────────

    def _register_all_endpoints(self) -> None:
        """
        Registers all buffered endpoints with FastAPI.
        Static paths are registered before parameterized ones to prevent routing conflicts.
        Example: /users/me must be registered before /users/{user_id}.
        """
        sorted_endpoints = sorted(
            self._pending_endpoints,
            key=lambda ep: ("{" in ep["path"], ep["path"]),
        )
        for ep in sorted_endpoints:
            self._register_endpoint(ep)

    def _register_endpoint(self, ep: dict) -> None:
        """
        Registers a single endpoint with FastAPI by building a compatible async wrapper.

        The wrapper captures the FastAPI Request and Response objects and delegates
        to the core request processing pipeline (_process_request).

        Path parameters (e.g. {user_id}) are extracted from the path template and
        injected into the wrapper's signature so FastAPI generates proper OpenAPI docs.
        """
        import re

        path = ep["path"]
        method = ep["method"].upper()
        handler = ep["handler"]
        tags = ep["tags"]
        request_model = ep["request_model"]
        response_model = ep["response_model"]
        auth_validator = ep["auth_validator"]
        has_files = ep.get("has_files", False)

        # Unique operation ID for OpenAPI
        clean_path = path.replace("/", "_").replace("{", "").replace("}", "")
        operation_id = f"{method.lower()}{clean_path}"

        # Extract path parameter names from the path template (e.g. "/profiles/{id}" → ["id"])
        path_param_names = re.findall(r"\{(\w+)\}", path)

        # Build the FastAPI-compatible wrapper.
        # Wrappers use **kwargs to accept FastAPI-injected path params at runtime.
        # __signature__ is overridden below to control what Swagger shows.
        if request_model and method == "GET":
            async def fastapi_wrapper(request: Request, params: request_model = Depends(), **kwargs):
                return await _process_request(
                    request,
                    params,
                    handler,
                    auth_validator,
                    self._paused_owners,
                    trusted_proxies=self._trusted_proxies,
                    custom_ip_header=self._custom_ip_header,
                    trust_cloudflare=self._trust_cloudflare,
                )
        elif has_files:
            # If we have files and a request model, we want the model fields to show up as Form fields.
            # We pass kwargs to _process_request which will contain both path params and Form params.
            async def fastapi_wrapper(request: Request, files: Optional[list[UploadFile]] = File(None), **kwargs):
                return await _process_request(
                    request,
                    kwargs,
                    handler,
                    auth_validator,
                    self._paused_owners,
                    files=files,
                    trusted_proxies=self._trusted_proxies,
                    custom_ip_header=self._custom_ip_header,
                    trust_cloudflare=self._trust_cloudflare,
                )
        elif request_model:
            async def fastapi_wrapper(request: Request, body: request_model = None, **kwargs):
                return await _process_request(
                    request,
                    body,
                    handler,
                    auth_validator,
                    self._paused_owners,
                    trusted_proxies=self._trusted_proxies,
                    custom_ip_header=self._custom_ip_header,
                    trust_cloudflare=self._trust_cloudflare,
                )
        else:
            async def fastapi_wrapper(request: Request, **kwargs):
                return await _process_request(
                    request,
                    None,
                    handler,
                    auth_validator,
                    self._paused_owners,
                    trusted_proxies=self._trusted_proxies,
                    custom_ip_header=self._custom_ip_header,
                    trust_cloudflare=self._trust_cloudflare,
                )

        # Override __signature__ to control OpenAPI documentation.
        # Always remove **kwargs; add explicit path params and Form params if present.
        sig = inspect.signature(fastapi_wrapper)
        params = [
            p for p in sig.parameters.values() if p.kind != inspect.Parameter.VAR_KEYWORD
        ]

        # 1. Add path parameters
        if path_param_names:
            path_params_list = [
                inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)
                for name in path_param_names
            ]
            # Insert path params after 'request'
            params = [params[0]] + path_params_list + params[1:]

        # 2. Add Form parameters if has_files and request_model
        if has_files and request_model:
            from fastapi import Form
            for field_name, field in request_model.model_fields.items():
                # Check if it's required (no default value)
                if field.is_required():
                    default_val = Form(...)
                else:
                    default_val = Form(field.default)
                
                params.append(
                    inspect.Parameter(
                        field_name,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        default=default_val,
                        annotation=field.annotation
                    )
                )

        # 3. Add a documentation-only Bearer security dependency for protected
        # routes, so Swagger UI (/docs) shows the lock icon + honors the
        # "Authorize" button by sending "Authorization: Bearer <token>".
        # Real auth is still enforced in _process_request; this is metadata only.
        if auth_validator:
            params.append(
                inspect.Parameter(
                    "_bearer_auth",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=Security(self._bearer_scheme),
                    annotation=Optional[HTTPAuthorizationCredentials],
                )
            )

        fastapi_wrapper.__signature__ = sig.replace(parameters=params)

        fastapi_wrapper.__name__ = operation_id
        self.app.add_api_route(
            path,
            fastapi_wrapper,
            methods=[method],
            tags=tags,
            response_model=response_model,
            operation_id=operation_id,
        )

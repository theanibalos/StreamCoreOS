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

    # Serve static files from a directory
    http.mount_static("/static", "./public")

    # WebSocket endpoint
    http.add_ws_endpoint(
        path="/ws/chat",
        on_connect=self.on_ws_connect,     # called when client connects (receives WebSocket)
        on_disconnect=self.on_ws_disconnect,  # optional, called on disconnect
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
        # If has_files=True, 'data["_files"]' contains the list of UploadFile objects.
        # 'context' is an HttpContext handle for response manipulation
        return {"success": True, "data": {...}}


RESPONSE CONTRACT:
────────────────────────────────────────────────────────────────────────────────

    # Success (HTTP 200 by default)
    return {"success": True, "data": {...}}

    # Business error (HTTP 200 — client checks the 'success' field)
    return {"success": False, "error": "User not found"}

    # Explicit HTTP status override via context
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

    context.set_status(code: int)           → Override HTTP status code (default: 200)
    context.set_cookie(key, value, ...)     → Set a response cookie
    context.set_header(key, value)          → Add a custom response header
    context.redirect(url, status=302)       → Redirect to another URL
    context.set_binary_response(content, media_type) → Return raw binary data


AUTH VALIDATOR CONTRACT:
────────────────────────────────────────────────────────────────────────────────

    async def _validate_token(self, token: str) -> dict | None:
        try:
            return self.auth.decode_token(token)   # Return payload dict on success
        except Exception:
            return None                            # Return None to trigger HTTP 401

    # The returned payload is injected into data["_auth"] for the handler to use.
    # The token is extracted from: Authorization: Bearer <token>  OR  Cookie: access_token=<token>


REPLACEMENT STANDARD (implement this to swap the backend):
────────────────────────────────────────────────────────────────────────────────

    To create an aiohttp-based implementation:

    1. Create tools/aiohttp_server/aiohttp_server_tool.py
    2. name = "http"                               ← same injection key, plugins are unaffected
    3. Implement the public methods:
          add_endpoint(path, method, handler, tags, request_model, response_model, auth_validator, has_files)
          mount_static(path, directory_path)
          add_ws_endpoint(path, on_connect, on_disconnect)
          add_sse_endpoint(path, generator, tags, auth_validator)
    4. Handler contract: handler(data: dict, context: HttpContext) → dict
       - data: flat merge of path params + query params + body (+ _files if applicable)
       - context: instance of HttpContext (or a compatible duck-type)
    5. Honor context.status_code and context.binary_content for the HTTP response
    6. For auth: call auth_validator(token), inject payload into data["_auth"]
    7. On auth failure: return HTTP 401 with {"success": False, "error": "..."}
    8. On unhandled exception: return HTTP 500 with {"success": False, "error": "Internal server error"}

    Plugins will NOT require any changes.
"""

import os
import uuid
import asyncio
import inspect
import uvicorn
from typing import Optional, Any, Callable
from pydantic import BaseModel
from fastapi.exceptions import RequestValidationError
from core.base_tool import BaseTool
from core.context import current_identity_var, current_event_id_var
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends, File, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool


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
# HTTP CONTEXT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HttpContext:
    """
    Response manipulation handle provided to every HTTP handler.
    Passed as the second argument: async def execute(self, data: dict, context: HttpContext)

    Use to override the status code, set cookies, or add custom headers.
    All mutations are applied to the response before it is sent to the client.
    """

    def __init__(self) -> None:
        self._status_code: int = 200
        self._cookies: list[dict] = []
        self._headers: dict[str, str] = {}

    def set_status(self, code: int) -> None:
        """
        Override the HTTP response status code. Default is 200.

        Examples:
            context.set_status(201)  # Created
            context.set_status(404)  # Not Found
            context.set_status(204)  # No Content
        """
        self._status_code = code

    def set_cookie(
        self,
        key: str,
        value: str,
        max_age: int = 3600,
        httponly: bool = True,
        samesite: str = "lax",
        secure: bool = True,
        path: str = "/",
    ) -> None:
        """
        Set a cookie on the HTTP response.
        
        Defaults:
            httponly=True: Prevents JavaScript access (XSS protection).
            samesite="lax": Prevents most CSRF attacks.
            secure=True: Cookie only sent over HTTPS. Set to False for local HTTP development.
        """
        self._cookies.append({
            "key": key,
            "value": value,
            "max_age": max_age,
            "httponly": httponly,
            "samesite": samesite,
            "secure": secure,
            "path": path,
        })

    def set_header(self, key: str, value: str) -> None:
        """Add a custom header to the HTTP response."""
        self._headers[key] = value

    def redirect(self, url: str, status: int = 302) -> None:
        """
        Redirect the browser to the given URL.
        The handler's return value is ignored when this is called.

        Example:
            context.redirect("http://localhost:5173/")
            context.redirect("/dashboard", status=301)
        """
        self._redirect_url = url
        self._status_code = status

    def apply_to(self, response: Any) -> None:
        """Apply all accumulated cookies and headers to the given response object."""
        for key, value in self._headers.items():
            response.headers[key] = value
        for cookie in self._cookies:
            response.set_cookie(**cookie)

    def set_binary_response(self, content: bytes, media_type: str = "application/octet-stream") -> None:
        """
        Instruct the tool to return raw binary data instead of the default JSON envelope.
        The handler's return value will be ignored.
        """
        self._binary_content = content
        self._media_type = media_type

    @property
    def binary_content(self) -> tuple[bytes, str] | None:
        content = getattr(self, "_binary_content", None)
        if content is not None:
            return content, getattr(self, "_media_type", "application/octet-stream")
        return None

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def redirect_url(self) -> str | None:
        return getattr(self, "_redirect_url", None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP SERVER TOOL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HttpServerTool(BaseTool):

    def __init__(self):
        self.app = FastAPI(title="MicroCoreOS Gateway")
        self._port: int = int(os.getenv("HTTP_PORT", 5000))
        self._server: Optional[uvicorn.Server] = None
        self._pending_endpoints: list[dict] = []

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

        cors_origins_raw = os.getenv("HTTP_CORS_ORIGINS", "*")
        cors_origins = [o.strip() for o in cors_origins_raw.split(",")] if cors_origins_raw != "*" else ["*"]
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )


    async def on_boot_complete(self, container) -> None:
        """
        Registers all buffered endpoints and starts the uvicorn server.
        Endpoints are buffered (not registered immediately in add_endpoint) to allow
        FastAPI to sort static paths before parameterized paths, preventing routing conflicts.
        """
        self._register_all_endpoints()
        host = os.getenv("HTTP_HOST", "127.0.0.1")
        log_level = os.getenv("HTTP_LOG_LEVEL", "warning")
        config = uvicorn.Config(self.app, host=host, port=self._port, log_level=log_level)
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
            - data["_files"]: list of FastAPI UploadFile objects (only if has_files=True).
        - SECURITY DEFAULTS:
            - Cookies set via context.set_cookie are 'Secure=True', 'HttpOnly=True', 'SameSite=Lax'.
            - CSRF Guard: Mutations (POST/PUT/DELETE) using cookie auth REQUIRE 'X-Requested-With' header.
        - CAPABILITIES:
            - add_endpoint(path, method, handler, tags=None, request_model=None,
                           response_model=None, auth_validator=None, has_files=False):
                - has_files: if True, enables multipart/form-data. Request model fields 
                  become Form fields. To use a file: file = data["_files"][0]; 
                  await s3.upload_fileobj(file.filename, file.file, content_type=file.content_type)
            - mount_static(path, directory_path): Serve static files from a directory.
            - add_ws_endpoint(path, on_connect, on_disconnect=None): WebSocket support.
            - add_sse_endpoint(path, generator, tags=None, auth_validator=None): 
                Server-Sent Events. generator yields formatted strings: "data: {...}\\n\\n".
        - HttpContext CAPABILITIES (inside handler):
            - context.set_status(code: int): Override HTTP status (default: 200).
            - context.redirect(url: str, status=302): Redirect to another URL.
            - context.set_cookie(key, value, max_age=3600, ...): Set secure response cookie.
            - context.set_header(key, value): Add custom response header.
            - context.set_binary_response(content: bytes, media_type: str): Return raw file.
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

    def mount_static(self, path: str, directory_path: str) -> None:
        """Serves static files from a local directory."""
        if os.path.exists(directory_path):
            self.app.mount(path, StaticFiles(directory=directory_path), name=path)

    def add_ws_endpoint(self, path: str, on_connect: Callable, on_disconnect: Optional[Callable] = None) -> None:
        """Registers a WebSocket endpoint."""
        @self.app.websocket(path)
        async def ws_handler(websocket: WebSocket):
            await websocket.accept()
            try:
                if inspect.iscoroutinefunction(on_connect):
                    await on_connect(websocket)
                else:
                    await run_in_threadpool(on_connect, websocket)
            except WebSocketDisconnect:
                if on_disconnect:
                    if inspect.iscoroutinefunction(on_disconnect):
                        await on_disconnect(websocket)
                    else:
                        await run_in_threadpool(on_disconnect, websocket)
            except Exception as e:
                print(f"[HttpServer] WebSocket error on {path}: {e}")
                if on_disconnect:
                    try:
                        if inspect.iscoroutinefunction(on_disconnect):
                            await on_disconnect(websocket)
                        else:
                            await run_in_threadpool(on_disconnect, websocket)
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
        from fastapi.responses import StreamingResponse

        async def sse_handler(request: Request):
            data: dict = {}
            data.update(request.query_params)
            data.update(request.path_params)

            if auth_validator:
                token = self._extract_bearer_token(request)
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

        clean_path = path.replace("/", "_")
        sse_handler.__name__ = f"sse{clean_path}"
        self.app.add_api_route(path, sse_handler, methods=["GET"], tags=tags or [])

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
                return await self._process_request(request, params, handler, auth_validator)
        elif has_files:
            # If we have files and a request model, we want the model fields to show up as Form fields.
            # We pass kwargs to _process_request which will contain both path params and Form params.
            async def fastapi_wrapper(request: Request, files: Optional[list[UploadFile]] = File(None), **kwargs):
                return await self._process_request(request, kwargs, handler, auth_validator, files=files)
        elif request_model:
            async def fastapi_wrapper(request: Request, body: request_model = None, **kwargs):
                return await self._process_request(request, body, handler, auth_validator)
        else:
            async def fastapi_wrapper(request: Request, **kwargs):
                return await self._process_request(request, None, handler, auth_validator)

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

    # ── Request processing pipeline ──────────────────────────────────────────────

    async def _process_request(
        self,
        request: Request,
        body_data: Any,
        handler: Callable,
        auth_validator: Optional[Callable],
        files: Optional[list] = None,
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
            data["_files"] = files

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
            context = HttpContext()

            # ── Phase 3: Authentication ────────────────────────────────────────
            if auth_validator:
                token = self._extract_bearer_token(request)
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

            print(
                f"[HttpServer] ← {request.method} {request.url.path}"
                f"  req={request_id[:8]}  status={context.status_code}"
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

            json_response = JSONResponse(status_code=context.status_code, content=_serialize(result))
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

    def _extract_bearer_token(self, request: Request) -> Optional[str]:
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

import time
import inspect
import contextlib
import threading
from collections import deque
from core.registry import Registry
from core.base_tool import ToolUnavailableError


class ToolNotFoundError(Exception):
    """Raised by Container.get() when no tool is registered under the given name."""


class ToolProxy:
    """
    Transparent proxy that wraps a Tool.
    Intercepts method calls to:
    - Track tool health in the Registry (see DEAD policy below).
    - Automatically restore OK status on the first successful call after a failure.
    - Measure and emit call duration to a metrics sink.
    - Create tracing spans when a span factory is registered.

    DEAD policy (hybrid):
    - ToolUnavailableError (or subclass) → DEAD immediately. The tool itself
      declared its infrastructure unreachable.
    - Any other exception → counted, not classified. Business errors (constraint
      violations, bad input) are indistinguishable from real failures one by one,
      so the proxy uses a streak: DEAD_THRESHOLD consecutive failures mark the
      tool DEAD. A single success resets the streak.
    """

    DEAD_THRESHOLD = 5

    def __init__(self, tool, registry: Registry, emit_metric=None, make_span=None):
        self._tool = tool
        self._registry = registry
        self._emit_metric = emit_metric
        self._make_span = make_span  # callable(tool, method) -> context manager
        self._wrapper_cache = {}
        self._consecutive_failures = 0

    def _record_success(self):
        self._consecutive_failures = 0
        if self._registry.get_tool_status(self._tool.name) == "DEAD":
            self._registry.update_tool_status(self._tool.name, "OK", "Recovered")

    def _record_failure(self, e: Exception):
        self._consecutive_failures += 1
        if isinstance(e, ToolUnavailableError):
            self._registry.update_tool_status(self._tool.name, "DEAD", str(e))
        elif self._consecutive_failures >= self.DEAD_THRESHOLD:
            self._registry.update_tool_status(
                self._tool.name, "DEAD",
                f"{self._consecutive_failures} consecutive failures. Last: {e}"
            )

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._tool, name, value)

    def __getattr__(self, name):
        if name in self._wrapper_cache:
            return self._wrapper_cache[name]

        attr = getattr(self._tool, name)

        if not callable(attr):
            return attr

        emit = self._emit_metric
        make_span = self._make_span
        tool_name = self._tool.name

        if inspect.iscoroutinefunction(attr):
            async def wrapper(*args, **kwargs):
                start = time.perf_counter()
                span_cm = make_span(tool_name, name) if make_span else contextlib.nullcontext()
                with span_cm:
                    try:
                        result = await attr(*args, **kwargs)
                        self._record_success()
                        if emit:
                            emit(tool_name, name, (time.perf_counter() - start) * 1000, True)
                        return result
                    except Exception as e:
                        if emit:
                            emit(tool_name, name, (time.perf_counter() - start) * 1000, False)
                        self._record_failure(e)
                        raise e
        else:
            def wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    result = attr(*args, **kwargs)

                    # Handle sync function returning an awaitable
                    if inspect.isawaitable(result):
                        async def _monitored():
                            inner_start = time.perf_counter()
                            span_cm = make_span(tool_name, name) if make_span else contextlib.nullcontext()
                            with span_cm:
                                try:
                                    r = await result
                                    self._record_success()
                                    if emit:
                                        emit(tool_name, name, (time.perf_counter() - inner_start) * 1000, True)
                                    return r
                                except Exception as e:
                                    if emit:
                                        emit(tool_name, name, (time.perf_counter() - inner_start) * 1000, False)
                                    self._record_failure(e)
                                    raise
                        return _monitored()

                    self._record_success()
                    if emit:
                        emit(tool_name, name, (time.perf_counter() - start) * 1000, True)
                    return result
                except Exception as e:
                    if emit:
                        emit(tool_name, name, (time.perf_counter() - start) * 1000, False)
                    self._record_failure(e)
                    raise e

        self._wrapper_cache[name] = wrapper
        return wrapper


class Container:
    """
    Service Locator for Tools.
    Single responsibility: register, get, and list tools.
    Health/metadata tracking is handled by Registry via ToolProxy.
    Metrics collection is handled by an internal ring buffer + sink list.
    Tracing spans are injected via a registrable span factory.
    """

    def __init__(self):
        self._tools = {}
        self._lock = threading.RLock()
        self.registry = Registry()
        self._metrics_sinks = []
        self._metrics_buffer = deque(maxlen=1000)
        self._span_factory = None

    # ── Metrics ───────────────────────────────────────────────────────────────

    def add_metrics_sink(self, callback):
        """Register a sink to receive metric records on every tool call.
        Signature: callback(record: dict) — record has: tool, method, duration_ms, success, timestamp.
        """
        self._metrics_sinks.append(callback)

    def get_metrics(self) -> list:
        """Return the last 1000 metric records (chronological order)."""
        return list(self._metrics_buffer)

    def _emit_metric(self, tool: str, method: str, duration_ms: float, success: bool):
        record = {
            "tool": tool,
            "method": method,
            "duration_ms": round(duration_ms, 3),
            "success": success,
            "timestamp": time.time(),
        }
        self._metrics_buffer.append(record)
        for sink in self._metrics_sinks:
            try:
                sink(record)
            except Exception as e:
                print(f"[Container] Metrics sink error: {e}")

    # ── Spans ─────────────────────────────────────────────────────────────────

    def register_span_factory(self, factory):
        """Register a span factory for tracing instrumentation (an
        observability tool provides the implementation).
        Signature: factory(tool: str, method: str) -> context manager.
        Safe to call after proxies are created — takes effect on the next tool call.
        """
        self._span_factory = factory

    def _get_span_cm(self, tool: str, method: str):
        if self._span_factory:
            return self._span_factory(tool, method)
        return contextlib.nullcontext()

    def get_raw_tools(self) -> list:
        """Return raw tool instances bypassing proxies.
        Lets an observability tool call on_instrument() without risking DEAD status.
        """
        with self._lock:
            return [proxy._tool for proxy in self._tools.values()]

    # ── Registration ──────────────────────────────────────────────────────────

    def register(self, tool):
        with self._lock:
            if hasattr(tool, '_set_core_registry'):
                tool._set_core_registry(self.registry)
            if hasattr(tool, '_set_container'):
                tool._set_container(self)
            self._tools[tool.name] = ToolProxy(
                tool, self.registry, self._emit_metric, self._get_span_cm
            )
        print(f"[Container] Tool registered (Proxied): {tool.name}")

    def get(self, name: str):
        with self._lock:
            if name not in self._tools:
                raise ToolNotFoundError(f"Tool '{name}' not found.")
            return self._tools[name]

    def has_tool(self, name: str) -> bool:
        with self._lock:
            return name in self._tools

    def list_tools(self):
        with self._lock:
            return list(self._tools.keys())

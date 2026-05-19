import time
import inspect
import asyncio
import contextlib
import threading
from collections import deque
from core.registry import Registry


class ToolProxy:
    """
    Transparent proxy that wraps a Tool.
    Intercepts method calls to:
    - Automatically retry failed calls (default: 2 retries).
    - Report DEAD status to the Registry on unhandled exceptions after retries fail.
    - Measure and emit call duration to a metrics sink.
    - Create OpenTelemetry spans when a span factory is registered.
    """
    def __init__(self, tool, registry: Registry, emit_metric=None, make_span=None, retries: int = 2):
        self._tool = tool
        self._registry = registry
        self._emit_metric = emit_metric
        self._make_span = make_span  # callable(tool, method) -> context manager
        self._retries = retries
        self._wrapper_cache = {}

    def __getattr__(self, name):
        if name in self._wrapper_cache:
            return self._wrapper_cache[name]

        attr = getattr(self._tool, name)

        if not callable(attr):
            return attr

        emit = self._emit_metric
        make_span = self._make_span
        registry = self._registry
        tool_name = self._tool.name
        max_retries = self._retries

        if inspect.iscoroutinefunction(attr):
            async def wrapper(*args, **kwargs):
                last_exception = None
                for attempt in range(max_retries + 1):
                    start = time.perf_counter()
                    span_cm = make_span(tool_name, name) if make_span else contextlib.nullcontext()
                    with span_cm:
                        try:
                            result = await attr(*args, **kwargs)
                            if registry.get_tool_status(tool_name) == "DEAD":
                                registry.update_tool_status(tool_name, "OK", "Recovered after retry")
                            if emit:
                                emit(tool_name, name, (time.perf_counter() - start) * 1000, True)
                            return result
                        except Exception as e:
                            last_exception = e
                            if attempt < max_retries:
                                # Small backoff could be added here
                                await asyncio.sleep(0.05 * (attempt + 1))
                                continue
                            
                            if emit:
                                emit(tool_name, name, (time.perf_counter() - start) * 1000, False)
                            registry.update_tool_status(tool_name, "DEAD", str(e))
                            raise last_exception
        else:
            def wrapper(*args, **kwargs):
                last_exception = None
                for attempt in range(max_retries + 1):
                    start = time.perf_counter()
                    try:
                        result = attr(*args, **kwargs)
                        
                        # Handle sync function returning an awaitable (common in some patterns)
                        if inspect.isawaitable(result):
                            async def _monitored():
                                inner_last_exc = None
                                # For awaitables returned by sync funcs, we'd need another loop 
                                # but usually these are already wrapped or thin. 
                                # Keeping original behavior for the awaitable part for simplicity,
                                # but applying the status logic.
                                inner_start = time.perf_counter()
                                span_cm = make_span(tool_name, name) if make_span else contextlib.nullcontext()
                                with span_cm:
                                    try:
                                        r = await result
                                        if registry.get_tool_status(tool_name) == "DEAD":
                                            registry.update_tool_status(tool_name, "OK", "Recovered")
                                        if emit:
                                            emit(tool_name, name, (time.perf_counter() - inner_start) * 1000, True)
                                        return r
                                    except Exception as e:
                                        if emit:
                                            emit(tool_name, name, (time.perf_counter() - inner_start) * 1000, False)
                                        registry.update_tool_status(tool_name, "DEAD", str(e))
                                        raise
                            return _monitored()

                        if registry.get_tool_status(tool_name) == "DEAD":
                            registry.update_tool_status(tool_name, "OK", "Recovered")
                        if emit:
                            emit(tool_name, name, (time.perf_counter() - start) * 1000, True)
                        return result
                    except Exception as e:
                        last_exception = e
                        if attempt < max_retries:
                            time.sleep(0.05 * (attempt + 1))
                            continue
                        
                        if emit:
                            emit(tool_name, name, (time.perf_counter() - start) * 1000, False)
                        registry.update_tool_status(tool_name, "DEAD", str(e))
                        raise last_exception

        self._wrapper_cache[name] = wrapper
        return wrapper


class Container:
    """
    Service Locator for Tools.
    Single responsibility: register, get, and list tools.
    Health/metadata tracking is handled by Registry via ToolProxy.
    Metrics collection is handled by an internal ring buffer + sink list.
    OTel spans are injected via a registrable span factory.
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

    # ── Spans (OTel) ──────────────────────────────────────────────────────────

    def register_span_factory(self, factory):
        """Register a span factory for OpenTelemetry instrumentation.
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
        Used by TelemetryTool to call on_instrument() without risking DEAD status.
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
                raise Exception(f"Tool '{name}' not found.")
            return self._tools[name]

    def has_tool(self, name: str) -> bool:
        with self._lock:
            return name in self._tools

    def list_tools(self):
        with self._lock:
            return list(self._tools.keys())

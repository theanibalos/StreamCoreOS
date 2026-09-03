import os
import contextlib
from microcoreos import BaseTool


class _NoOpTracer:
    """Fallback tracer returned when OTel is disabled or not installed."""
    def start_as_current_span(self, name, **kwargs):
        return contextlib.nullcontext()


class _NoOpInstrument:
    """Fallback metric instrument — accepts record()/add() calls and drops them."""
    def record(self, value, **kwargs):
        pass

    def add(self, value, **kwargs):
        pass


class _NoOpMeter:
    """Fallback meter returned when OTel is disabled or not installed."""
    def create_histogram(self, name, **kwargs):
        return _NoOpInstrument()

    def create_counter(self, name, **kwargs):
        return _NoOpInstrument()


class TelemetryTool(BaseTool):
    """
    OpenTelemetry distributed tracing and metrics tool for MicroCoreOS.

    Auto-instruments ALL tool calls via ToolProxy — no changes needed in plugins or tools.
    Optionally instruments underlying frameworks (FastAPI, asyncpg) via on_instrument() hooks.

    Activation: set OTEL_ENABLED=true in environment.
    Degrades gracefully if disabled or if opentelemetry packages are not installed.
    """

    @property
    def name(self) -> str:
        return "telemetry"

    async def setup(self):
        self._tracer_provider = None
        self._meter_provider = None
        self._enabled = os.getenv("OTEL_ENABLED", "false").lower() == "true"

        if not self._enabled:
            print("[TelemetryTool] Disabled. Set OTEL_ENABLED=true to activate.")
            return

        try:
            from opentelemetry import trace, metrics
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.resources import Resource

            service_name = os.getenv("OTEL_SERVICE_NAME", "microcoreos")
            endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

            resource = Resource.create({"service.name": service_name})
            provider = TracerProvider(resource=resource)

            if endpoint:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
                meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))],
                )
                print(f"[TelemetryTool] Exporting to {endpoint} (service: {service_name})")
            else:
                from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
                from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
                provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
                meter_provider = MeterProvider(
                    resource=resource,
                    metric_readers=[PeriodicExportingMetricReader(ConsoleMetricExporter())],
                )
                print(f"[TelemetryTool] Console exporter active (service: {service_name}). "
                      "Set OTEL_EXPORTER_OTLP_ENDPOINT for production.")

            trace.set_tracer_provider(provider)
            self._tracer_provider = provider

            metrics.set_meter_provider(meter_provider)
            self._meter_provider = meter_provider

        except ImportError as e:
            print(f"[TelemetryTool] WARNING: OTEL_ENABLED=true but packages missing: {e}")
            print("[TelemetryTool] Install: uv add opentelemetry-sdk opentelemetry-exporter-otlp")
            self._enabled = False

    async def on_boot_complete(self, container):
        if not self._enabled or not self._tracer_provider:
            return

        # 1. Register span factory — all future tool calls get a span automatically.
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("microcoreos.proxy")

            def span_factory(tool: str, method: str):
                return tracer.start_as_current_span(
                    f"{tool}.{method}",
                    attributes={"tool": tool, "method": method},
                )

            container.register_span_factory(span_factory)
        except Exception as e:
            print(f"[TelemetryTool] Failed to register span factory: {e}")
            return

        # 1b. Register a metrics sink — every tool call already timed by ToolProxy
        #     (the same record that feeds registry.get_metrics()) also becomes an
        #     OTel histogram + counter, with zero changes to plugins or tools.
        try:
            from opentelemetry import metrics
            meter = metrics.get_meter("microcoreos.proxy")
            duration_histogram = meter.create_histogram(
                "tool_call_duration_ms",
                unit="ms",
                description="Duration of tool method calls",
            )
            call_counter = meter.create_counter(
                "tool_call_total",
                description="Number of tool method calls",
            )

            def metrics_sink(record: dict):
                attributes = {
                    "tool": record["tool"],
                    "method": record["method"],
                    "success": record["success"],
                }
                duration_histogram.record(record["duration_ms"], attributes=attributes)
                call_counter.add(1, attributes=attributes)

            container.add_metrics_sink(metrics_sink)
        except Exception as e:
            print(f"[TelemetryTool] Failed to register metrics sink: {e}")

        # 2. Call on_instrument() on each raw tool instance for driver-level spans.
        #    Runs bypassing ToolProxy so a failure here never marks a tool as DEAD.
        for raw_tool in container.get_raw_tools():
            if raw_tool.name == self.name:
                continue
            try:
                await raw_tool.on_instrument(self._tracer_provider)
            except Exception as e:
                print(f"[TelemetryTool] on_instrument() failed for '{raw_tool.name}': {e}")

    def get_tracer(self, scope: str):
        """Get a named tracer for custom spans inside a plugin.
        Returns a no-op tracer if OTel is disabled.

        Usage:
            tracer = self.telemetry.get_tracer("orders")
            with tracer.start_as_current_span("process_payment"):
                ...
        """
        if not self._enabled:
            return _NoOpTracer()
        try:
            from opentelemetry import trace
            return trace.get_tracer(scope)
        except ImportError:
            return _NoOpTracer()

    def get_meter(self, scope: str):
        """Get a named meter for custom metrics inside a plugin.
        Returns a no-op meter if OTel is disabled.

        Usage:
            meter = self.telemetry.get_meter("orders")
            counter = meter.create_counter("orders_created")
            counter.add(1)
        """
        if not self._enabled:
            return _NoOpMeter()
        try:
            from opentelemetry import metrics
            return metrics.get_meter(scope)
        except ImportError:
            return _NoOpMeter()

    async def shutdown(self):
        if self._tracer_provider:
            try:
                self._tracer_provider.shutdown()
            except Exception:
                pass
        if self._meter_provider:
            try:
                self._meter_provider.shutdown()
            except Exception:
                pass

    def get_interface_description(self) -> str:
        return """
        Telemetry Tool (telemetry):
        - PURPOSE: OpenTelemetry distributed tracing AND metrics. Auto-instruments all tool
          calls via ToolProxy. No changes needed in plugins or existing tools to get basic
          spans or metrics.
        - ACTIVATION: Set OTEL_ENABLED=true. Degrades gracefully if disabled or packages missing.
        - ENV VARS:
            - OTEL_ENABLED: "true" to activate (default: "false").
            - OTEL_SERVICE_NAME: Service name in traces/metrics (default: "microcoreos").
            - OTEL_EXPORTER_OTLP_ENDPOINT: OTLP/gRPC endpoint (e.g. "http://otel-collector:4317").
              If not set, traces and metrics are printed to console (development mode).
        - CAPABILITIES:
            - get_tracer(scope: str) -> Tracer: Named tracer for custom spans inside a plugin.
                Usage: tracer = self.telemetry.get_tracer("my_plugin")
                       with tracer.start_as_current_span("my_operation"): ...
                Returns a no-op tracer if OTel is disabled — safe to use unconditionally.
            - get_meter(scope: str) -> Meter: Named meter for custom metrics inside a plugin.
                Usage: meter = self.telemetry.get_meter("my_plugin")
                       counter = meter.create_counter("orders_created")
                       counter.add(1)
                Returns a no-op meter if OTel is disabled — safe to use unconditionally.
        - AUTO-INSTRUMENTATION (zero config):
            Every tool call (db.execute, event_bus.publish, auth.create_token, etc.)
            gets a span automatically via ToolProxy, AND is recorded as an OTel histogram
            (tool_call_duration_ms) and counter (tool_call_total) with tool/method/success
            attributes — the same record already exposed at registry.get_metrics() / GET
            /system/metrics, now also exported over OTLP. No plugin changes needed.
        - DRIVER-LEVEL INSTRUMENTATION (optional, per tool):
            Tools can implement on_instrument(tracer_provider) in BaseTool to add
            framework-specific spans (SQL query text, HTTP route, etc.).
        - INSTALL:
            uv add opentelemetry-sdk opentelemetry-exporter-otlp
        """

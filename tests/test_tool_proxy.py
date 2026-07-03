import pytest
from core.container import ToolProxy, Container
from core.base_tool import ToolUnavailableError
from core.registry import Registry

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend():
    return "asyncio"

class FailingTool:
    @property
    def name(self) -> str:
        return "unstable_tool"

    def sync_fail(self):
        raise ValueError("sync error")

    async def async_fail(self):
        raise ValueError("async error")

    def sync_infra_fail(self):
        raise ToolUnavailableError("backend unreachable")

    async def async_infra_fail(self):
        raise ToolUnavailableError("backend unreachable")

    def sync_ok(self):
        return "ok"

    async def async_ok(self):
        return "ok"

@pytest.fixture
def registry():
    r = Registry()
    r.register_tool("unstable_tool", "OK")
    return r

# ─── Status Update Tests (Hybrid DEAD Policy) ────────────────────────────────

def test_single_business_failure_does_not_mark_dead(registry):
    """A lone generic exception (e.g. UNIQUE violation) must NOT kill the tool."""
    proxy = ToolProxy(FailingTool(), registry)

    with pytest.raises(ValueError, match="sync error"):
        proxy.sync_fail()

    assert registry.get_tool_status("unstable_tool") == "OK"

async def test_single_async_business_failure_does_not_mark_dead(registry):
    proxy = ToolProxy(FailingTool(), registry)

    with pytest.raises(ValueError, match="async error"):
        await proxy.async_fail()

    assert registry.get_tool_status("unstable_tool") == "OK"

def test_consecutive_failures_mark_dead(registry):
    """DEAD_THRESHOLD consecutive generic failures mark the tool DEAD."""
    proxy = ToolProxy(FailingTool(), registry)

    for _ in range(ToolProxy.DEAD_THRESHOLD):
        with pytest.raises(ValueError):
            proxy.sync_fail()

    assert registry.get_tool_status("unstable_tool") == "DEAD"

def test_success_resets_failure_streak(registry):
    """A success in the middle of failures resets the consecutive counter."""
    proxy = ToolProxy(FailingTool(), registry)

    for _ in range(ToolProxy.DEAD_THRESHOLD - 1):
        with pytest.raises(ValueError):
            proxy.sync_fail()

    proxy.sync_ok()  # resets the streak

    with pytest.raises(ValueError):
        proxy.sync_fail()

    assert registry.get_tool_status("unstable_tool") == "OK"

def test_sync_infra_failure_marks_dead_immediately(registry):
    """ToolUnavailableError marks DEAD on the FIRST failure — no threshold."""
    proxy = ToolProxy(FailingTool(), registry)

    with pytest.raises(ToolUnavailableError):
        proxy.sync_infra_fail()

    assert registry.get_tool_status("unstable_tool") == "DEAD"

async def test_async_infra_failure_marks_dead_immediately(registry):
    proxy = ToolProxy(FailingTool(), registry)

    with pytest.raises(ToolUnavailableError):
        await proxy.async_infra_fail()

    assert registry.get_tool_status("unstable_tool") == "DEAD"

def test_proxy_sync_recovery_marks_ok(registry):
    registry.update_tool_status("unstable_tool", "DEAD", "old error")
    proxy = ToolProxy(FailingTool(), registry)

    res = proxy.sync_ok()

    assert res == "ok"
    assert registry.get_tool_status("unstable_tool") == "OK"

async def test_proxy_async_recovery_marks_ok(registry):
    registry.update_tool_status("unstable_tool", "DEAD", "old error")
    proxy = ToolProxy(FailingTool(), registry)

    res = await proxy.async_ok()

    assert res == "ok"
    assert registry.get_tool_status("unstable_tool") == "OK"

# ─── Metrics Collection Tests ────────────────────────────────────────────────

def test_proxy_emits_metrics(registry):
    metrics = []

    def emit_metric(tool, method, duration_ms, success):
        metrics.append({
            "tool": tool,
            "method": method,
            "success": success
        })

    proxy = ToolProxy(FailingTool(), registry, emit_metric=emit_metric)

    proxy.sync_ok()
    try:
        proxy.sync_fail()
    except ValueError:
        pass

    assert len(metrics) == 2

    assert metrics[0]["tool"] == "unstable_tool"
    assert metrics[0]["method"] == "sync_ok"
    assert metrics[0]["success"] is True

    assert metrics[1]["tool"] == "unstable_tool"
    assert metrics[1]["method"] == "sync_fail"
    assert metrics[1]["success"] is False

# ─── Pruebas a Nivel Contenedor ──────────────────────────────────────────────

def test_container_metrics_sink():
    container = Container()

    class SimpleTool:
        name = "simple"
        def do_work(self): pass

    container.register(SimpleTool())

    sink_records = []
    def my_sink(record):
        sink_records.append(record)

    container.add_metrics_sink(my_sink)

    proxy = container.get("simple")
    proxy.do_work()

    # Verificar que el sink haya recibido el registro
    assert len(sink_records) == 1
    assert sink_records[0]["tool"] == "simple"
    assert sink_records[0]["method"] == "do_work"
    assert sink_records[0]["success"] is True

    # Verificar que el buffer interno del contenedor lo haya guardado
    history = container.get_metrics()
    assert len(history) == 1
    assert history[0]["method"] == "do_work"


def test_proxy_setattr_delegation(registry):
    class SimpleTool:
        name = "simple"
        def __init__(self):
            self.value = 42

    tool = SimpleTool()
    proxy = ToolProxy(tool, registry)

    # Set attribute on proxy
    proxy.value = 100

    # Ensure it was set on the underlying tool instance
    assert tool.value == 100
    # Ensure getting the attribute via proxy also reflects the new value
    assert proxy.value == 100

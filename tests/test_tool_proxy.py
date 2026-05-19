import pytest
from core.container import ToolProxy, Container
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
        
    def sync_ok(self):
        return "ok"
        
    async def async_ok(self):
        return "ok"

@pytest.fixture
def registry():
    r = Registry()
    r.register_tool("unstable_tool", "OK")
    return r

# ─── Pruebas de Actualización de Estado (Observabilidad) ─────────────────────

def test_proxy_sync_failure_marks_dead(registry):
    proxy = ToolProxy(FailingTool(), registry)
    
    with pytest.raises(ValueError, match="sync error"):
        proxy.sync_fail()
        
    assert registry.get_tool_status("unstable_tool") == "DEAD"

async def test_proxy_async_failure_marks_dead(registry):
    proxy = ToolProxy(FailingTool(), registry)
    
    with pytest.raises(ValueError, match="async error"):
        await proxy.async_fail()
        
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

# ─── Pruebas de Recolección de Métricas ──────────────────────────────────────

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
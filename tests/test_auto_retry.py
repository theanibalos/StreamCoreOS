import pytest
import asyncio
from core.container import ToolProxy
from core.registry import Registry

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend():
    return "asyncio"

class FlakyTool:
    def __init__(self):
        self.calls = 0
        self.name = "flaky_tool"
        
    async def work(self):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError(f"Fail {self.calls}")
        return "success"
        
    def sync_work(self):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError(f"Fail {self.calls}")
        return "success"

@pytest.fixture
def registry():
    r = Registry()
    r.register_tool("flaky_tool", "OK")
    return r

async def test_auto_retry_async_success(registry):
    """
    Verifica que un método asíncrono que falla las primeras 2 veces
    funcione en el 3er intento gracias al Auto-Retry del Kernel.
    """
    tool = FlakyTool()
    # 2 retries significa 3 intentos en total
    proxy = ToolProxy(tool, registry, retries=2)
    
    result = await proxy.work()
    
    assert result == "success"
    assert tool.calls == 3
    assert registry.get_tool_status("flaky_tool") == "OK"

def test_auto_retry_sync_success(registry):
    """
    Verifica que un método síncrono también se beneficie del Auto-Retry.
    """
    tool = FlakyTool()
    proxy = ToolProxy(tool, registry, retries=2)
    
    result = proxy.sync_work()
    
    assert result == "success"
    assert tool.calls == 3
    assert registry.get_tool_status("flaky_tool") == "OK"

async def test_auto_retry_exhausted_marks_dead(registry):
    """
    Verifica que si se agotan los reintentos, el tool finalmente se marque como DEAD.
    """
    tool = FlakyTool()
    # Solo permitimos 1 reintento, pero el tool necesita 3 intentos para funcionar
    proxy = ToolProxy(tool, registry, retries=1)
    
    with pytest.raises(RuntimeError, match="Fail 2"):
        await proxy.work()
        
    assert tool.calls == 2
    assert registry.get_tool_status("flaky_tool") == "DEAD"

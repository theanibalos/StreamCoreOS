import pytest
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
        raise RuntimeError(f"Fail {self.calls}")
        
    def sync_work(self):
        self.calls += 1
        raise RuntimeError(f"Fail {self.calls}")

@pytest.fixture
def registry():
    r = Registry()
    r.register_tool("flaky_tool", "OK")
    return r

async def test_no_auto_retry_async(registry):
    """
    Verify that the Kernel NO LONGER retries automatically.
    Should fail on the first attempt.
    """
    tool = FlakyTool()
    proxy = ToolProxy(tool, registry)
    
    with pytest.raises(RuntimeError, match="Fail 1"):
        await proxy.work()

    assert tool.calls == 1
    # Hybrid DEAD policy: a single generic failure does not kill the tool
    assert registry.get_tool_status("flaky_tool") == "OK"

def test_no_auto_retry_sync(registry):
    """
    Verify that the synchronous method also fails on the first attempt.
    """
    tool = FlakyTool()
    proxy = ToolProxy(tool, registry)
    
    with pytest.raises(RuntimeError, match="Fail 1"):
        proxy.sync_work()

    assert tool.calls == 1
    # Hybrid DEAD policy: a single generic failure does not kill the tool
    assert registry.get_tool_status("flaky_tool") == "OK"

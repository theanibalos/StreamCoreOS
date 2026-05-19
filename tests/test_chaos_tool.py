import pytest
from extras.available_tools.chaos.chaos_tool import ChaosTool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_setup_no_env():
    tool = ChaosTool()
    await tool.setup()


async def test_setup_chaos_disabled(monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "false")
    tool = ChaosTool()
    await tool.setup()


async def test_setup_chaos_enabled(monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    tool = ChaosTool()
    with pytest.raises(RuntimeError, match="BOOM"):
        await tool.setup()


def test_name():
    tool = ChaosTool()
    assert tool.name == "chaos"

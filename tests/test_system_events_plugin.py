import asyncio
import pytest
from unittest.mock import MagicMock
from tools.event_bus.event_bus_tool import EventBusTool, EventEnvelope
from domains.system.plugins.system_events_plugin import SystemEventsPlugin

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def event_bus():
    bus = EventBusTool()
    await bus.setup()
    try:
        yield bus
    finally:
        await bus.shutdown()


async def test_system_events_registers_both_endpoints():
    mock_http = MagicMock()
    mock_bus = MagicMock()
    plugin = SystemEventsPlugin(http=mock_http, event_bus=mock_bus)
    await plugin.on_boot()

    endpoints = [call[0][0] for call in mock_http.add_endpoint.call_args_list]
    assert "/system/events" in endpoints
    assert "/api/system/events" in endpoints


async def test_system_events_discovers_static_publishers(event_bus):
    mock_http = MagicMock()
    plugin = SystemEventsPlugin(http=mock_http, event_bus=event_bus)

    result = await plugin.execute({})
    assert result["success"] is True

    events_by_name = {e.event: e for e in result["data"]["events"]}
    
    # We know domains/stream_state/plugins/stream_status_plugin.py publishes "stream.session.started"
    assert "stream.session.started" in events_by_name
    entry = events_by_name["stream.session.started"]
    assert entry.times_fired == 0
    assert any("StreamStatusPlugin" in emitter for emitter in entry.last_emitters)


async def test_times_fired_counts_publications_and_reports_emitters(event_bus):
    async def handler(event: EventEnvelope):
        pass

    await event_bus.subscribe("test.counter", handler)
    from tools.event_bus.event_bus_tool import current_identity_var
    token = current_identity_var.set("unit_test_emitter")
    try:
        await event_bus.publish("test.counter", {"data": 123})
    finally:
        current_identity_var.reset(token)
    await asyncio.sleep(0.05)

    plugin = SystemEventsPlugin(http=MagicMock(), event_bus=event_bus)
    result = await plugin.execute({})
    assert result["success"] is True

    events_by_name = {e.event: e for e in result["data"]["events"]}
    assert "test.counter" in events_by_name
    entry = events_by_name["test.counter"]
    assert entry.times_fired == 1
    assert "unit_test_emitter" in entry.last_emitters

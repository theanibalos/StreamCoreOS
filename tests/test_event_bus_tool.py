import asyncio
import pytest
from tools.event_bus.event_bus_tool import EventBusTool, EventEnvelope

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend(): return "asyncio"

@pytest.fixture
async def bus():
    b = EventBusTool()
    await b.setup()
    yield b
    await b.shutdown()

async def test_subscribe_publish(bus):
    received = []
    async def handler(event: EventEnvelope):
        received.append(event.payload)

    await bus.subscribe("user.created", handler)
    await bus.publish("user.created", {"id": 1})
    await asyncio.sleep(0.01)
    assert received == [{"id": 1}]

async def test_envelope_metadata(bus):
    received = []
    async def handler(event: EventEnvelope):
        received.append(event)

    await bus.subscribe("meta.test", handler)
    await bus.publish("meta.test", {"x": 1})
    await asyncio.sleep(0.01)
    
    env = received[0]
    assert env.event == "meta.test"
    assert env.payload == {"x": 1}
    assert env.id is not None
    assert env.timestamp is not None

async def test_request_response(bus):
    async def handler(event: EventEnvelope):
        return {"ok": True, "echo": event.payload["msg"]}

    await bus.subscribe("validate", handler)
    result = await bus.request("validate", {"msg": "hello"})
    assert result == {"ok": True, "echo": "hello"}

async def test_wildcard_observability(bus):
    received = []
    await bus.subscribe("*", lambda env: received.append(env.event))
    await bus.publish("a", {})
    await bus.publish("b", {})
    await asyncio.sleep(0.01)
    assert "a" in received
    assert "b" in received

async def test_causality_chain(bus):
    async def parent_handler(event: EventEnvelope):
        await bus.publish("child", {"p": event.id})

    await bus.subscribe("parent", parent_handler)
    await bus.publish("parent", {})
    await asyncio.sleep(0.05)

    history = bus.get_trace_history()
    parent_rec = next(r for r in history if r.envelope.event == "parent")
    child_rec = next(r for r in history if r.envelope.event == "child")
    assert child_rec.envelope.parent_id == parent_rec.envelope.id

async def test_dead_subscriber_auto_unsubscribe(bus):
    calls = []
    async def flaky(event: EventEnvelope):
        calls.append(1)
        raise RuntimeError("always fails")

    await bus.subscribe("boom", flaky)

    for _ in range(EventBusTool._MAX_CONSECUTIVE_FAILURES):
        await bus.publish("boom", {})

    await asyncio.sleep(0.1)

    # After max failures, the handler must be gone — bus must not deadlock
    before = len(calls)
    await bus.publish("boom", {})
    await asyncio.sleep(0.05)
    assert len(calls) == before  # no new calls — handler was removed

async def test_auto_unsubscribe_publishes_dropped_event(bus):
    dropped = []
    async def on_dropped(event: EventEnvelope):
        dropped.append(event)

    async def flaky(event: EventEnvelope):
        raise RuntimeError("always fails")

    await bus.subscribe(EventBusTool.SUBSCRIBER_DROPPED_EVENT, on_dropped)
    await bus.subscribe("boom", flaky)

    for _ in range(EventBusTool._MAX_CONSECUTIVE_FAILURES):
        await bus.publish("boom", {})
    await asyncio.sleep(0.1)

    assert len(dropped) == 1
    payload = dropped[0].payload
    assert payload["event"] == "boom"
    assert "flaky" in payload["subscriber"]
    assert payload["error"] == "always fails"
    assert payload["consecutive_failures"] == EventBusTool._MAX_CONSECUTIVE_FAILURES

async def test_dropped_event_subscriber_drop_does_not_retrigger(bus):
    # A failing subscriber OF the dropped event must not re-trigger it (loop guard).
    max_fails = EventBusTool._MAX_CONSECUTIVE_FAILURES

    async def broken_monitor(event: EventEnvelope):
        raise RuntimeError("monitor down")

    def make_flaky():
        async def flaky(event: EventEnvelope):
            raise RuntimeError("always fails")
        return flaky

    await bus.subscribe(EventBusTool.SUBSCRIBER_DROPPED_EVENT, broken_monitor)

    # Drop N distinct subscribers -> N dropped events -> broken_monitor fails
    # on each of them and is itself dropped on the Nth. The guard must prevent
    # a further dropped event about broken_monitor (no self-reference).
    for i in range(max_fails):
        event_name = f"boom.{i}"
        await bus.subscribe(event_name, make_flaky())
        for _ in range(max_fails):
            await bus.publish(event_name, {})
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    await asyncio.sleep(0.2)
    history = bus.get_trace_history()
    published_dropped = [
        r for r in history
        if r.kind == "published" and r.envelope.event == EventBusTool.SUBSCRIBER_DROPPED_EVENT
    ]
    # One per flaky subscriber; broken_monitor's own drop is guarded and silent.
    assert len(published_dropped) == max_fails
    assert all(
        "broken_monitor" not in r.envelope.payload["subscriber"]
        for r in published_dropped
    )

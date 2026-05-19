import asyncio
import pytest
from tools.event_bus.event_bus_tool import EventBusTool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bus():
    b = EventBusTool()
    await b.setup()
    yield b
    await b.shutdown()


# ── subscribe/publish ──────────────────────────────────────────────────────────

async def test_subscribe_publish(bus):
    received = []

    async def handler(data):
        received.append(data)

    await bus.subscribe("user.created", handler)
    await bus.publish("user.created", {"id": 1})
    await asyncio.sleep(0.01)

    assert received == [{"id": 1}]


async def test_publish_no_subscribers(bus):
    await bus.publish("ghost.event", {"x": 1})
    await asyncio.sleep(0.01)


async def test_unsubscribe(bus):
    received = []

    async def handler(data):
        received.append(data)

    await bus.subscribe("evt", handler)
    await bus.publish("evt", {"n": 1})
    await asyncio.sleep(0.01)
    assert len(received) == 1

    await bus.unsubscribe("evt", handler)
    await bus.publish("evt", {"n": 2})
    await asyncio.sleep(0.01)
    assert len(received) == 1


async def test_wildcard_receives_all(bus):
    received = []

    async def wildcard_handler(data):
        received.append(data)

    await bus.subscribe("*", wildcard_handler)
    await bus.publish("a.event", {"x": 1})
    await bus.publish("b.event", {"y": 2})
    await asyncio.sleep(0.01)

    assert len(received) == 2


async def test_async_subscriber(bus):
    result = []

    async def handler(data):
        await asyncio.sleep(0.01)
        result.append(data["v"])

    await bus.subscribe("tick", handler)
    await bus.publish("tick", {"v": 42})
    await asyncio.sleep(0.05)

    assert result == [42]


async def test_sync_subscriber(bus):
    result = []

    def sync_handler(data):
        result.append(data["v"])

    await bus.subscribe("tick", sync_handler)
    await bus.publish("tick", {"v": 7})
    await asyncio.sleep(0.1)

    assert result == [7]


# ── request/response (RPC) ────────────────────────────────────────────────────

async def test_request_response(bus):
    async def handler(data):
        return {"ok": True, "echo": data["msg"]}

    await bus.subscribe("validate", handler)
    result = await bus.request("validate", {"msg": "hello"})

    assert result == {"ok": True, "echo": "hello"}


async def test_request_timeout(bus):
    with pytest.raises(asyncio.TimeoutError):
        await bus.request("no.subscriber", {}, timeout=0.1)


async def test_request_subscriber_returning_none_causes_timeout(bus):
    async def bad_handler(data):
        return None

    await bus.subscribe("query", bad_handler)
    with pytest.raises(asyncio.TimeoutError):
        await bus.request("query", {}, timeout=0.1)


async def test_wildcard_cannot_reply_in_rpc(bus):
    wildcard_received = []

    async def wildcard_handler(data):
        wildcard_received.append(data)
        return {"should": "be ignored"}

    await bus.subscribe("*", wildcard_handler)
    with pytest.raises(asyncio.TimeoutError):
        await bus.request("rpc.event", {"q": 1}, timeout=0.1)

    await asyncio.sleep(0.05)
    assert len(wildcard_received) == 1


# ── observabilidad ────────────────────────────────────────────────────────────

async def test_trace_history_after_publish(bus):
    await bus.publish("traced.event", {"k": "v"})
    await asyncio.sleep(0.01)

    history = bus.get_trace_history()
    assert any(r["event"] == "traced.event" for r in history)


async def test_trace_record_fields(bus):
    await bus.publish("fielded.event", {"key1": 1, "key2": 2})
    await asyncio.sleep(0.01)

    history = bus.get_trace_history()
    record = next(r for r in history if r["event"] == "fielded.event")

    for field in ("id", "event", "emitter", "subscribers", "payload_keys", "timestamp"):
        assert field in record


async def test_get_subscribers(bus):
    class MyHandler:
        async def on_event(self, data):
            pass

    obj = MyHandler()
    await bus.subscribe("my.event", obj.on_event)

    subs = bus.get_subscribers()
    assert "my.event" in subs
    assert subs["my.event"] == ["MyHandler.on_event"]


async def test_add_listener(bus):
    trace_records = []

    def listener(record):
        trace_records.append(record)

    bus.add_listener(listener)
    await bus.publish("observed.event", {"x": 1})
    await asyncio.sleep(0.01)

    assert len(trace_records) == 1
    assert trace_records[0]["event"] == "observed.event"


async def test_add_listener_dedup(bus):
    calls = []

    def listener(record):
        calls.append(1)

    bus.add_listener(listener)
    bus.add_listener(listener)
    await bus.publish("dedup.event", {})
    await asyncio.sleep(0.01)

    assert len(calls) == 1


# ── resiliencia ───────────────────────────────────────────────────────────────

async def test_failure_listener(bus):
    failures = []

    def on_failure(record):
        failures.append(record)

    async def bad_handler(data):
        raise ValueError("intentional")

    bus.add_failure_listener(on_failure)
    await bus.subscribe("bad.event", bad_handler)
    await bus.publish("bad.event", {"x": 1})
    await asyncio.sleep(0.05)

    assert len(failures) == 1
    assert failures[0]["event"] == "bad.event"
    assert "subscriber" in failures[0]
    assert "error" in failures[0]


async def test_auto_unsubscribe_after_max_failures(bus):
    async def bad_handler(data):
        raise RuntimeError("always fails")

    await bus.subscribe("fail.event", bad_handler)

    for _ in range(EventBusTool._MAX_CONSECUTIVE_FAILURES):
        await bus.publish("fail.event", {})
        await asyncio.sleep(0.05)

    await asyncio.sleep(0.1)

    subs = bus.get_subscribers()
    assert "fail.event" not in subs


async def test_causality_parent_id(bus):
    async def parent_handler(data):
        await bus.publish("child.event", {"from": "parent"})

    await bus.subscribe("parent.event", parent_handler)
    await bus.publish("parent.event", {"x": 1})
    await asyncio.sleep(0.05)

    history = bus.get_trace_history()
    parent_record = next(r for r in history if r["event"] == "parent.event")
    child_record = next(r for r in history if r["event"] == "child.event")

    assert child_record["parent_id"] == parent_record["id"]


async def test_failure_listener_record_has_event_id(bus):
    failures = []
    bus.add_failure_listener(lambda r: failures.append(r))

    async def bad_handler(data):
        raise ValueError("test")

    await bus.subscribe("fail.rpc", bad_handler)
    await bus.publish("fail.rpc", {})
    await asyncio.sleep(0.05)

    assert len(failures) == 1
    assert "event_id" in failures[0]
    assert isinstance(failures[0]["event_id"], str)
    assert len(failures[0]["event_id"]) > 0


async def test_unsubscribe_nonexistent_handler_is_noop(bus):
    async def handler(data): pass
    # Nunca se hizo subscribe — no debe lanzar
    await bus.unsubscribe("evento.fantasma", handler)


async def test_get_subscribers_removes_key_after_last_unsubscribe(bus):
    async def handler(data): pass
    await bus.subscribe("solo.event", handler)
    assert "solo.event" in bus.get_subscribers()

    await bus.unsubscribe("solo.event", handler)
    assert "solo.event" not in bus.get_subscribers()

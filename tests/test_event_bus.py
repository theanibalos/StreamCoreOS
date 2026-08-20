"""
Tests for EventBusTool — the system's central nervous system.

Covers: publish/subscribe, wildcard, RPC (request/response),
RPC timeout, failure handling, auto-unsubscribe of dead handlers,
failure listeners, and trace history.

NOTE: subscribers now receive an EventEnvelope (event.payload holds the dict
that used to be passed directly) — updated for the MicroCoreOS sync
(Enterprise Event Bus rewrite: Pydantic envelopes, consumer groups, DLQ).
"""
import asyncio
import pytest
from tools.event_bus.event_bus_tool import EventBusTool


@pytest.fixture
def bus():
    return EventBusTool()


# ─── Publish / Subscribe ───────────────────────────────────────────────────

class TestPubSub:
    @pytest.mark.anyio
    async def test_subscriber_receives_event(self, bus):
        received = []
        async def handler(event): received.append(event.payload)

        await bus.subscribe("user.created", handler)
        await bus.publish("user.created", {"id": 1})
        await asyncio.sleep(0.01)  # allow tasks to run

        assert received == [{"id": 1}]

    @pytest.mark.anyio
    async def test_multiple_subscribers_all_receive(self, bus):
        calls = []
        async def h1(event): calls.append("h1")
        async def h2(event): calls.append("h2")

        await bus.subscribe("ev", h1)
        await bus.subscribe("ev", h2)
        await bus.publish("ev", {})
        await asyncio.sleep(0.01)

        assert "h1" in calls
        assert "h2" in calls

    @pytest.mark.anyio
    async def test_unsubscribe_stops_delivery(self, bus):
        received = []
        async def handler(event): received.append(event.payload)

        await bus.subscribe("ev", handler)
        await bus.unsubscribe("ev", handler)
        await bus.publish("ev", {"x": 1})
        await asyncio.sleep(0)

        assert received == []

    @pytest.mark.anyio
    async def test_no_subscribers_publish_is_safe(self, bus):
        # Should not raise
        await bus.publish("orphan.event", {"x": 1})
        await asyncio.sleep(0)

    @pytest.mark.anyio
    async def test_unrelated_event_not_delivered(self, bus):
        received = []
        async def handler(event): received.append(event.payload)

        await bus.subscribe("ev.a", handler)
        await bus.publish("ev.b", {"x": 1})
        await asyncio.sleep(0)

        assert received == []


# ─── System-wide observation (add_listener) ────────────────────────────────
# There is deliberately no wildcard subscription: system-wide observation is
# add_listener()'s job in-process (a publish-side sink, zero transport cost),
# and the broker's own tooling when distributed.

class TestListenerObservation:
    @pytest.mark.anyio
    async def test_listener_receives_all_events(self, bus):
        seen = []
        bus.add_listener(lambda record: seen.append(record["payload"].get("_type")))

        await bus.publish("a", {"_type": "a"})
        await bus.publish("b", {"_type": "b"})
        await asyncio.sleep(0.01)

        assert "a" in seen
        assert "b" in seen

    @pytest.mark.anyio
    async def test_listener_does_not_participate_in_rpc(self, bus):
        """add_listener() is a passive sink and must not reply to request() calls."""
        listener_called = []

        def listener(record):
            listener_called.append(True)

        bus.add_listener(listener)

        # No direct subscriber → request should timeout
        with pytest.raises(asyncio.TimeoutError):
            await bus.request("some.event", {}, timeout=0.05)

        assert listener_called  # the listener saw the publish
        # but it never subscribes/replies, so the request still times out


# ─── RPC (request / response) ────────────────────────────────────────────

class TestRPC:
    @pytest.mark.anyio
    async def test_request_returns_subscriber_response(self, bus):
        async def responder(event):
            return {"result": event.payload["value"] * 2}

        await bus.subscribe("math.double", responder)
        response = await bus.request("math.double", {"value": 21}, timeout=1)

        assert response == {"result": 42}

    @pytest.mark.anyio
    async def test_request_timeout_raises(self, bus):
        with pytest.raises(asyncio.TimeoutError):
            await bus.request("no.one.listening", {}, timeout=0.05)

    @pytest.mark.anyio
    async def test_request_first_responder_wins(self, bus):
        """First subscriber to return a value wins the RPC."""
        async def slow(event):
            await asyncio.sleep(10)
            return {"from": "slow"}

        async def fast(event):
            return {"from": "fast"}

        await bus.subscribe("race", slow)
        await bus.subscribe("race", fast)
        result = await bus.request("race", {}, timeout=1)

        assert result["from"] == "fast"

    @pytest.mark.anyio
    async def test_request_subscriber_returning_none_does_not_reply(self, bus):
        """A subscriber returning None should not satisfy the request."""
        async def silent(event):
            return None  # no reply

        async def real(event):
            return {"ok": True}

        await bus.subscribe("ev", silent)
        await bus.subscribe("ev", real)
        result = await bus.request("ev", {}, timeout=1)

        assert result == {"ok": True}


# ─── Failure handling ─────────────────────────────────────────────────────

class TestFailureHandling:
    @pytest.mark.anyio
    async def test_failing_subscriber_does_not_crash_bus(self, bus):
        """A subscriber that raises must not prevent other subscribers from running."""
        good_received = []

        async def bad(event): raise ValueError("boom")
        async def good(event): good_received.append(event.payload)

        await bus.subscribe("ev", bad)
        await bus.subscribe("ev", good)
        await bus.publish("ev", {"x": 1})
        await asyncio.sleep(0.05)

        assert good_received == [{"x": 1}]

    @pytest.mark.anyio
    async def test_failure_listener_is_called_on_subscriber_error(self, bus):
        failures = []

        async def bad(event): raise RuntimeError("test error")
        bus.add_failure_listener(lambda record: failures.append(record))

        await bus.subscribe("ev", bad)
        await bus.publish("ev", {})
        await asyncio.sleep(0.05)

        assert len(failures) == 1
        assert "bad" in failures[0]["subscriber"]
        assert "test error" in failures[0]["error"]

    @pytest.mark.anyio
    async def test_auto_unsubscribe_after_max_failures(self, bus):
        """A handler that fails 5 times in a row should be auto-unsubscribed."""
        call_count = []

        async def always_fails(event):
            call_count.append(1)
            raise RuntimeError("permanent failure")

        await bus.subscribe("ev", always_fails)

        # Trigger MAX_CONSECUTIVE_FAILURES (5) + a few more
        for _ in range(7):
            await bus.publish("ev", {})
            await asyncio.sleep(0.05)

        # Should have been called exactly 5 times before auto-unsubscribe
        assert len(call_count) == 5
        assert "always_fails" not in str(bus.get_subscribers())


# ─── Observability ────────────────────────────────────────────────────────

class TestObservability:
    @pytest.mark.anyio
    async def test_trace_history_records_events(self, bus):
        await bus.publish("trace.test", {"key": "value"})
        await asyncio.sleep(0)

        history = bus.get_trace_history()
        assert any(r.envelope.event == "trace.test" for r in history)

    @pytest.mark.anyio
    async def test_trace_history_max_500(self, bus):
        for i in range(510):
            await bus.publish(f"ev.{i}", {})
        await asyncio.sleep(0)

        assert len(bus.get_trace_history()) <= 500

    @pytest.mark.anyio
    async def test_get_subscribers_reflects_current_state(self, bus):
        async def h(event): pass

        await bus.subscribe("my.event", h)
        subs = bus.get_subscribers()

        assert "my.event" in subs
        assert any("h" in name for name in subs["my.event"])

    @pytest.mark.anyio
    async def test_add_listener_called_on_every_publish(self, bus):
        records = []
        bus.add_listener(lambda r: records.append(r))

        await bus.publish("ev.one", {"a": 1})
        await bus.publish("ev.two", {"b": 2})
        await asyncio.sleep(0)

        events = [r["event"] for r in records]
        assert "ev.one" in events
        assert "ev.two" in events

    @pytest.mark.anyio
    async def test_trace_contains_payload_keys(self, bus):
        await bus.publish("data.event", {"foo": 1, "bar": 2})
        await asyncio.sleep(0)

        history = bus.get_trace_history()
        record = next(r for r in history if r.envelope.event == "data.event")
        assert set(record.envelope.payload.keys()) == {"foo", "bar"}

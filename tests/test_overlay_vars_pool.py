"""Tests for the overlay dynamic vars pool (overlay.vars.set) and needs detection."""
import json
import asyncio
import pytest

from domains.overlays.plugins.overlay_stream_plugin import OverlayStreamPlugin


class FakeDb:
    def __init__(self):
        self.rows: dict[str, str] = {}
        self.executed: list[tuple[str, list]] = []

    async def query(self, sql, params=None):
        if "overlay_vars" in sql:
            return [{"key": k, "value": v} for k, v in self.rows.items()]
        return []

    async def query_one(self, sql, params=None):
        return None

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "overlay_vars" in sql and params:
            self.rows[params[0]] = params[1]
        return 1


class FakeState:
    def get(self, key, default=None, namespace="default"):
        return default


class FakeBus:
    def __init__(self):
        self.subs: dict[str, list] = {}

    async def subscribe(self, event, cb):
        self.subs.setdefault(event, []).append(cb)

    async def publish(self, event, data):
        for cb in self.subs.get(event, []):
            await cb(data)


class FakeTwitch:
    def on_event(self, event, cb):
        pass


class FakeHttp:
    def add_sse_endpoint(self, *a, **kw):
        pass


class FakeLogger:
    def error(self, msg):
        pass


def make_plugin():
    return OverlayStreamPlugin(
        http=FakeHttp(), db=FakeDb(), state=FakeState(),
        event_bus=FakeBus(), twitch=FakeTwitch(), logger=FakeLogger(),
    )


@pytest.mark.anyio
async def test_vars_set_persists_and_broadcasts():
    plugin = make_plugin()
    await plugin.on_boot()

    queue = asyncio.Queue(maxsize=10)
    plugin._registry["1"] = {
        "needs_stats": True, "needs_chat": False, "needs_alerts": False,
        "queues": [queue],
    }

    await plugin.bus.publish("overlay.vars.set", {"juego.actual": "Elden Ring", "meta.donos": 5})

    assert plugin._vars["juego.actual"] == "Elden Ring"
    assert plugin._vars["meta.donos"] == 5
    # Persisted as JSON in overlay_vars
    assert json.loads(plugin.db.rows["juego.actual"]) == "Elden Ring"
    # Broadcast to stats consumers
    msg = json.loads(queue.get_nowait())
    assert msg["type"] == "stats"
    assert msg["data"]["juego.actual"] == "Elden Ring"


@pytest.mark.anyio
async def test_vars_loaded_on_boot_and_included_in_stats():
    plugin = make_plugin()
    plugin.db.rows["followers.latest_name"] = json.dumps("Nick")
    await plugin.on_boot()

    stats = await plugin._current_stats()
    assert stats["followers.latest_name"] == "Nick"


@pytest.mark.anyio
async def test_follow_event_sets_latest_follower_var():
    plugin = make_plugin()
    await plugin.on_boot()

    await plugin._on_twitch_event({
        "_event_type": "channel.follow",
        "user_name": "StreamFan123", "user_login": "streamfan123",
    })

    assert plugin._vars["followers.latest_name"] == "StreamFan123"


def test_resolve_needs_reads_explicit_config():
    plugin = make_plugin()
    config = {
        "needs": {"stats": True, "chat": False, "alerts": True},
        "elements": [{"type": "banner"}],
    }
    needs = plugin._resolve_needs(config)
    assert needs == {"needs_stats": True, "needs_chat": False, "needs_alerts": True}


def test_resolve_needs_defaults_to_nothing_when_missing():
    plugin = make_plugin()
    # No `needs` field (overlay never re-saved in the builder) → no channels.
    needs = plugin._resolve_needs({"elements": [{"type": "chat_highlight"}]})
    assert needs == {"needs_stats": False, "needs_chat": False, "needs_alerts": False}


@pytest.mark.anyio
async def test_ignores_invalid_vars_payload():
    plugin = make_plugin()
    await plugin._on_vars_set("not-a-dict")
    assert plugin._vars == {}

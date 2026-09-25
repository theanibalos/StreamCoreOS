import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domains.overlays.plugins.overlay_feed_plugin import OverlayFeedPlugin
from domains.overlays.plugins.overlay_test_plugin import OverlayTestPlugin


def make_feed():
    db = AsyncMock()
    db.query_one.return_value = {"token": "secret"}
    db.query.return_value = []
    twitch = MagicMock()
    twitch.get_session.return_value = None
    return OverlayFeedPlugin(MagicMock(), db, AsyncMock(), twitch, MagicMock())


def message(queue):
    return json.loads(queue.get_nowait())


@pytest.mark.anyio
async def test_feed_forwards_redemption_and_youtube_monetization():
    feed = make_feed()
    queue = asyncio.Queue()
    feed._queues.append(queue)
    await feed._on_twitch_event({
        "_event_type": "channel.channel_points_custom_reward_redemption.add",
        "user_name": "viewer", "reward": {"title": "Hydrate", "cost": 250},
        "user_input": "please",
    })
    redemption = message(queue)
    assert redemption["type"] == "event.redemption"
    assert redemption["data"]["reward_name"] == "Hydrate"
    await feed._on_monetization(SimpleNamespace(payload={
        "platform": "youtube", "type": "superchat", "message_id": "m1",
        "user": {"id": "youtube:u1", "display_name": "viewer"},
        "amount_micros": 1000000, "currency": "USD", "display_amount": "$1", "message": "hi",
    }))
    superchat = message(queue)
    assert superchat["type"] == "event.superchat"
    assert superchat["data"]["amount_micros"] == 1000000


@pytest.mark.anyio
async def test_feed_snapshot_includes_persisted_vars_and_rejects_bad_token():
    feed = make_feed()
    feed.db.query.return_value = [{"key": "game.score", "value": "42"}]
    stream = feed._stream({"token": "secret"})
    snapshot = json.loads((await anext(stream)).removeprefix("data: "))
    assert snapshot["type"] == "feed.snapshot"
    assert snapshot["data"]["stats"]["game.score"] == 42
    await stream.aclose()
    denied = feed._stream({"token": "bad"})
    error = json.loads((await anext(denied)).removeprefix("data: "))
    assert error["type"] == "feed.error"


@pytest.mark.anyio
async def test_rotating_token_closes_existing_stream():
    feed = make_feed()
    stream = feed._stream({"token": "secret"})
    await anext(stream)
    feed.db.query_one.return_value = {"token": "new-secret"}
    feed._queues[0].put_nowait(feed._envelope("event.follow", {"user": "viewer"}))
    error = json.loads((await anext(stream)).removeprefix("data: "))
    assert error["type"] == "feed.error"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.anyio
async def test_system_events_include_fields_needed_by_local_overlays():
    feed = make_feed()
    queue = asyncio.Queue()
    feed._queues.append(queue)
    handler = feed._system_handler("stream.session.started")
    await handler(SimpleNamespace(payload={"session_id": 7, "started_at": "now", "broadcaster_login": "caster"}))
    event = message(queue)
    assert event["type"] == "stream.session.started"
    assert event["data"]["session_id"] == 7
    assert event["data"]["broadcaster_login"] == "caster"


@pytest.mark.anyio
async def test_redemption_test_uses_requested_reward_name():
    bus = AsyncMock()
    twitch = MagicMock()
    twitch.get_session.return_value = {"access_token": "session"}
    plugin = OverlayTestPlugin(MagicMock(), bus, twitch, MagicMock())
    result = await plugin.execute({"type": "event.redemption", "reward_name": "tlabaja"})
    assert result["success"]
    assert bus.publish.call_args.args[1]["data"]["reward_name"] == "tlabaja"


@pytest.mark.anyio
async def test_chat_resolves_emotes_and_channel_badges():
    feed = make_feed()
    feed.twitch.get_session.return_value = {"access_token": "session"}
    feed.twitch.get = AsyncMock(side_effect=[
        {"data": [{"set_id": "moderator", "versions": [{"id": "1", "image_url_2x": "https://cdn.test/mod.png"}]}]},
        {"data": [{"set_id": "subscriber", "versions": [{"id": "6", "image_url_2x": "https://cdn.test/sub.png"}]}]},
    ])
    queue = asyncio.Queue()
    feed._queues.append(queue)
    payload = {
        "platform": "twitch", "channel_id": "123", "message_id": "m1",
        "user": {"id": "u1", "display_name": "Viewer"}, "message": "Kappa hello",
        "badges": [{"set": "moderator", "version": "1"}, {"set": "subscriber", "version": "6"}],
        "fragments": [{"type": "emote", "text": "Kappa", "emote_id": "25"},
                      {"type": "text", "text": " hello"}],
    }
    await feed._on_chat(SimpleNamespace(payload=payload))
    chat = message(queue)
    assert chat["data"]["badges"][0]["url"] == "https://cdn.test/mod.png"
    assert chat["data"]["badges"][1]["url"] == "https://cdn.test/sub.png"
    assert chat["data"]["fragments"][0]["url"].endswith("/25/static/dark/2.0")
    await feed._on_chat(SimpleNamespace(payload=payload))
    assert feed.twitch.get.await_count == 2

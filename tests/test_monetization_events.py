from unittest.mock import AsyncMock, MagicMock

import pytest

from domains.subscribers.plugins.bits_tracker_plugin import BitsTrackerPlugin
from domains.youtube_chat.plugins.youtube_chat_poller_plugin import YouTubeChatPollerPlugin


@pytest.mark.anyio
async def test_bits_tracker_publishes_normalized_monetization_event():
    bus = AsyncMock()
    plugin = BitsTrackerPlugin(twitch=MagicMock(), db=AsyncMock(), event_bus=bus, logger=MagicMock())

    await plugin._record("u1", "login", "Display", 100, "b1")

    topic, payload = bus.publish.call_args_list[-1].args
    assert topic == "monetization.event.received"
    assert payload["platform"] == "twitch"
    assert payload["channel_id"] == "b1"
    assert payload["type"] == "bits"
    assert payload["user"]["id"] == "twitch:u1"


@pytest.mark.anyio
async def test_youtube_poller_publishes_superchat_as_normalized_monetization_event():
    bus = AsyncMock()
    plugin = YouTubeChatPollerPlugin(youtube=MagicMock(), event_bus=bus, db=AsyncMock(), logger=MagicMock())

    await plugin._publish_monetization({
        "id": "msg1",
        "snippet": {
            "liveChatId": "live1",
            "superChatDetails": {
                "amountMicros": 1230000,
                "currency": "USD",
                "amountDisplayString": "$1.23",
            },
        },
    }, "superChatEvent", "Display", "youtube:UC1", "hello")

    bus.publish.assert_called_once()
    topic, payload = bus.publish.call_args.args
    assert topic == "monetization.event.received"
    assert payload["platform"] == "youtube"
    assert payload["channel_id"] == "live1"
    assert payload["type"] == "superchat"
    assert payload["amount_micros"] == 1230000

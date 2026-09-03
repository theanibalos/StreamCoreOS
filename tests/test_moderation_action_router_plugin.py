from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from domains.moderation.plugins.moderation_action_router_plugin import ModerationActionRouterPlugin


def _event(payload):
    return SimpleNamespace(payload=payload)


@pytest.fixture
def tools():
    twitch = AsyncMock()
    twitch.get_session = MagicMock(return_value={"broadcaster_id": "b1", "access_token": "tok"})
    youtube = AsyncMock()
    return {
        "twitch": twitch,
        "youtube": youtube,
        "event_bus": AsyncMock(),
        "db": AsyncMock(),
        "logger": MagicMock(),
    }


@pytest.mark.anyio
async def test_router_applies_twitch_timeout_and_logs(tools):
    plugin = ModerationActionRouterPlugin(**tools)

    await plugin._on_requested(_event({
        "platform": "twitch",
        "channel_id": "b1",
        "user": {"id": "twitch:u1", "platform_id": "u1", "display_name": "User"},
        "action": "timeout",
        "duration_s": 60,
        "reason": "test",
        "rule_id": 7,
    }))

    tools["twitch"].post.assert_called_once()
    tools["db"].execute.assert_called_once()
    tools["event_bus"].publish.assert_called_once()
    assert tools["event_bus"].publish.call_args.args[0] == "moderation.action.taken"


@pytest.mark.anyio
async def test_router_applies_youtube_delete(tools):
    plugin = ModerationActionRouterPlugin(**tools)

    await plugin._on_requested(_event({
        "platform": "youtube",
        "channel_id": "live-chat-id",
        "message_id": "msg1",
        "user": {"id": "youtube:UC1", "platform_id": "UC1", "display_name": "User"},
        "action": "delete",
        "reason": "test",
    }))

    tools["youtube"].delete_message.assert_called_once_with("msg1")
    tools["db"].execute.assert_called_once()

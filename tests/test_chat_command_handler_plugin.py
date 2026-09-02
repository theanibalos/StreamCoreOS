import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
from domains.chat_bot.plugins.chat_command_handler_plugin import ChatCommandHandlerPlugin


def _event(payload):
    return SimpleNamespace(payload=payload)


@pytest.fixture
def mock_tools():
    return {
        "twitch": AsyncMock(),
        "event_bus": AsyncMock(),
        "db": AsyncMock(),
        "state": AsyncMock(),
        "logger": MagicMock(),
    }


@pytest.fixture
def plugin(mock_tools):
    return ChatCommandHandlerPlugin(**mock_tools)


def _base_command(**overrides):
    cmd = {
        "name": "!so", "response": "¡Vayan a ver a {touser}! 🎉",
        "cooldown_s": 0, "global_cooldown_s": 0, "userlevel": "everyone",
        "use_count": 0, "enabled": 1, "action": None,
    }
    cmd.update(overrides)
    return cmd


@pytest.mark.anyio
async def test_shoutout_action_calls_twitch_shoutouts_endpoint(plugin, mock_tools):
    mock_tools["twitch"].get_session = MagicMock(return_value={
        "access_token": "tok", "broadcaster_id": "1", "refresh_token": "r", "login": "me",
    })
    mock_tools["twitch"].get.return_value = {"data": [{"id": "999", "login": "otheruser"}]}

    await plugin._do_shoutout({"args": "@otheruser"}, "ch")

    mock_tools["twitch"].get.assert_called_once_with(
        "/users", params={"login": "otheruser"}, user_token="tok"
    )
    mock_tools["twitch"].post.assert_called_once()
    (endpoint,), kwargs = mock_tools["twitch"].post.call_args
    assert endpoint.startswith("/chat/shoutouts?")
    assert "from_broadcaster_id=1" in endpoint
    assert "to_broadcaster_id=999" in endpoint
    assert "moderator_id=1" in endpoint
    assert kwargs["user_token"] == "tok"


@pytest.mark.anyio
async def test_shoutout_action_noop_without_target(plugin, mock_tools):
    mock_tools["twitch"].get_session = MagicMock(return_value={
        "access_token": "tok", "broadcaster_id": "1", "refresh_token": "r", "login": "me",
    })

    await plugin._do_shoutout({"args": ""}, "ch")

    mock_tools["twitch"].get.assert_not_called()
    mock_tools["twitch"].post.assert_not_called()


@pytest.mark.anyio
async def test_handle_triggers_shoutout_for_action_command(plugin, mock_tools):
    mock_tools["db"].query_one.return_value = _base_command(action="shoutout")
    mock_tools["db"].execute.return_value = 1
    mock_tools["state"].get.return_value = None
    mock_tools["twitch"].get_session = MagicMock(return_value={
        "access_token": "tok", "broadcaster_id": "1", "refresh_token": "r", "login": "me",
    })
    mock_tools["twitch"].get.return_value = {"data": [{"id": "999", "login": "otheruser"}]}

    await plugin._handle(_event({
        "command": "!so", "args": "@otheruser", "user_id": "42",
        "display_name": "Mod", "channel": "ch", "is_mod": True, "badges": {},
    }))

    mock_tools["twitch"].post.assert_called_once()
    mock_tools["twitch"].send_message.assert_not_called()
    mock_tools["event_bus"].publish.assert_any_call("chat.message.send", {
        "platform": "twitch",
        "channel_id": "ch",
        "channel_name": None,
        "message": "¡Vayan a ver a otheruser! 🎉",
    })


@pytest.mark.anyio
async def test_handle_skips_shoutout_for_plain_text_command(plugin, mock_tools):
    mock_tools["db"].query_one.return_value = _base_command(action=None, response="Hola {user}!")
    mock_tools["db"].execute.return_value = 1
    mock_tools["state"].get.return_value = None

    await plugin._handle(_event({
        "command": "!so", "args": "", "user_id": "42",
        "display_name": "Mod", "channel": "ch", "is_mod": True, "badges": {},
    }))

    mock_tools["twitch"].post.assert_not_called()
    mock_tools["twitch"].get.assert_not_called()
    mock_tools["twitch"].send_message.assert_not_called()
    mock_tools["event_bus"].publish.assert_any_call("chat.message.send", {
        "platform": "twitch",
        "channel_id": "ch",
        "channel_name": None,
        "message": "Hola Mod!",
    })

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
from domains.chat_bot.plugins.echo_reminder_plugin import EchoReminderPlugin

@pytest.fixture
def mock_tools():
    return {
        "scheduler": MagicMock(),
        "twitch": AsyncMock(),
        "state": MagicMock(),
        "event_bus": AsyncMock(),
        "logger": MagicMock()
    }

@pytest.fixture
def plugin(mock_tools):
    return EchoReminderPlugin(**mock_tools)

@pytest.mark.anyio
async def test_echo_parsing_and_scheduling(plugin, mock_tools):
    mock_tools["state"].get.return_value = 0
    data = {
        "command": "!echo",
        "args": "10s Test message",
        "channel": "test_channel",
        "display_name": "TestUser",
        "is_mod": True,
        "badges": {}
    }
    
    await plugin._on_command(data)
    mock_tools["scheduler"].add_one_shot.assert_called_once()
    kwargs = mock_tools["scheduler"].add_one_shot.call_args.kwargs
    assert kwargs["message"] == "Test message"
    assert kwargs["channel"] == "test_channel"
    diff = (kwargs["run_at"] - datetime.now()).total_seconds()
    assert 9 <= diff <= 11
    mock_tools["twitch"].send_message.assert_called_with(
        "test_channel",
        "@TestUser Mensaje programado para dentro de 10s. 😊"
    )

@pytest.mark.anyio
async def test_echo_reminder_synonym_accepted(plugin, mock_tools):
    mock_tools["state"].get.return_value = 0
    data = {
        "command": "!reminder",
        "args": "10s Synonym test",
        "channel": "ch",
        "display_name": "User",
        "is_mod": True,
        "badges": {}
    }
    
    await plugin._on_command(data)
    mock_tools["scheduler"].add_one_shot.assert_called_once()
    kwargs = mock_tools["scheduler"].add_one_shot.call_args.kwargs
    assert kwargs["message"] == "Synonym test"

@pytest.mark.anyio
async def test_echo_permissions_denied(plugin, mock_tools):
    data = {
        "command": "!echo",
        "args": "10s Test",
        "is_mod": False,
        "is_broadcaster": False,
        "badges": {}
    }
    
    await plugin._on_command(data)
    mock_tools["scheduler"].add_one_shot.assert_not_called()

@pytest.mark.anyio
async def test_echo_vip_accepted(plugin, mock_tools):
    mock_tools["state"].get.return_value = 0
    data = {
        "command": "!echo",
        "args": "10s Test",
        "is_mod": False,
        "is_broadcaster": False,
        "badges": {"vip": "1"},
        "channel": "ch",
        "display_name": "VIPUser"
    }
    
    await plugin._on_command(data)
    mock_tools["scheduler"].add_one_shot.assert_called_once()

@pytest.mark.anyio
async def test_echo_limit_reached(plugin, mock_tools):
    # Simulate 3 pending echoes
    mock_tools["state"].get.return_value = 3
    data = {
        "command": "!echo",
        "args": "10s Test",
        "channel": "ch",
        "display_name": "ModUser",
        "is_mod": True,
        "badges": {}
    }
    
    await plugin._on_command(data)
    
    # Verify error message
    mock_tools["twitch"].send_message.assert_called_with(
        "ch",
        "@ModUser Falló la programación: Se alcanzó el límite máximo de 3 eco simultáneos. ❌"
    )
    mock_tools["scheduler"].add_one_shot.assert_not_called()

@pytest.mark.anyio
async def test_echo_counter_lifecycle(plugin, mock_tools):
    mock_tools["state"].get.return_value = 0
    data = {"command": "!echo", "args": "10s T", "channel": "ch", "is_mod": True, "display_name": "U", "badges": {}}
    await plugin._on_command(data)
    
    mock_tools["state"].set.assert_called_with("echo_count", 1, namespace="echo")
    
    mock_tools["state"].get.return_value = 1
    await plugin._send_echo("ch", "T")
    
    mock_tools["state"].set.assert_any_call("echo_count", 0, namespace="echo")

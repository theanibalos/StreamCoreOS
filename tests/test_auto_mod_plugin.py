from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from domains.moderation.plugins.auto_mod_plugin import AutoModPlugin
from domains.moderation.plugins.create_mod_rule_plugin import CreateModRulePlugin
from domains.moderation.plugins.update_mod_rule_plugin import UpdateModRulePlugin
from domains.moderation.plugins.list_mod_rules_plugin import ListModRulesPlugin


def _event(payload):
    return SimpleNamespace(payload=payload)


@pytest.fixture
def mock_tools():
    event_bus = AsyncMock()
    db = AsyncMock()
    state = AsyncMock()
    logger = MagicMock()
    http = MagicMock()
    return {
        "event_bus": event_bus,
        "db": db,
        "state": state,
        "logger": logger,
        "http": http,
    }


@pytest.mark.anyio
async def test_auto_mod_target_user_blocks_links_for_specific_user(mock_tools):
    plugin = AutoModPlugin(
        event_bus=mock_tools["event_bus"],
        db=mock_tools["db"],
        state=mock_tools["state"],
        logger=mock_tools["logger"],
    )

    # Rule #1 targets only 'spammer_user'
    rules = [
        {
            "id": 1,
            "type": "link_filter",
            "value": None,
            "action": "timeout",
            "duration_s": 300,
            "exempt_roles": "mod",
            "target_users": "spammer_user",
            "exempt_users": "",
        }
    ]

    async def get_state(key, default=None, namespace=None):
        if namespace == "moderation_rules" and key == "rules":
            return rules
        if namespace == "moderation_regulars" and key == "regulars":
            return set()
        return default

    mock_tools["state"].get.side_effect = get_state

    # 1. Normal user posts a link -> should NOT trigger moderation
    await plugin._on_message(_event({
        "platform": "twitch",
        "channel_id": "c1",
        "message_id": "m1",
        "message": "Check out https://google.com",
        "user": {
            "id": "twitch:100",
            "platform_id": "100",
            "login": "friendly_user",
            "display_name": "Friendly_User",
        },
        "roles": {},
    }))
    mock_tools["event_bus"].publish.assert_not_called()

    # 2. Targeted user posts the link -> SHOULD trigger moderation
    await plugin._on_message(_event({
        "platform": "twitch",
        "channel_id": "c1",
        "message_id": "m2",
        "message": "Check out https://google.com",
        "user": {
            "id": "twitch:200",
            "platform_id": "200",
            "login": "spammer_user",
            "display_name": "Spammer_User",
        },
        "roles": {},
    }))

    mock_tools["event_bus"].publish.assert_called_once()
    topic, payload = mock_tools["event_bus"].publish.call_args.args
    assert topic == "moderation.action.requested"
    assert payload["action"] == "timeout"
    assert payload["duration_s"] == 300
    assert payload["user"]["platform_id"] == "200"


@pytest.mark.anyio
async def test_auto_mod_exempt_user_bypasses_general_filter(mock_tools):
    plugin = AutoModPlugin(
        event_bus=mock_tools["event_bus"],
        db=mock_tools["db"],
        state=mock_tools["state"],
        logger=mock_tools["logger"],
    )

    # Global link filter, but 'whitelisted_friend' is exempt
    rules = [
        {
            "id": 2,
            "type": "link_filter",
            "value": None,
            "action": "delete",
            "duration_s": None,
            "exempt_roles": "mod",
            "target_users": "",
            "exempt_users": "whitelisted_friend",
        }
    ]

    async def get_state(key, default=None, namespace=None):
        if namespace == "moderation_rules" and key == "rules":
            return rules
        return default

    mock_tools["state"].get.side_effect = get_state

    # 1. Exempt user posts link -> NOT moderated
    await plugin._on_message(_event({
        "platform": "twitch",
        "channel_id": "c1",
        "message_id": "m1",
        "message": "https://friend.com",
        "user": {
            "id": "twitch:300",
            "platform_id": "300",
            "login": "whitelisted_friend",
            "display_name": "Whitelisted_Friend",
        },
        "roles": {},
    }))
    mock_tools["event_bus"].publish.assert_not_called()

    # 2. Other user posts link -> IS moderated
    await plugin._on_message(_event({
        "platform": "twitch",
        "channel_id": "c1",
        "message_id": "m2",
        "message": "https://friend.com",
        "user": {
            "id": "twitch:400",
            "platform_id": "400",
            "login": "random_user",
            "display_name": "Random_User",
        },
        "roles": {},
    }))
    mock_tools["event_bus"].publish.assert_called_once()
    assert mock_tools["event_bus"].publish.call_args.args[1]["action"] == "delete"


@pytest.mark.anyio
async def test_create_and_list_rules_with_target_and_exempt_users(mock_tools):
    create_plugin = CreateModRulePlugin(
        http=mock_tools["http"],
        event_bus=mock_tools["event_bus"],
        db=mock_tools["db"],
        logger=mock_tools["logger"],
    )
    list_plugin = ListModRulesPlugin(
        http=mock_tools["http"],
        db=mock_tools["db"],
        logger=mock_tools["logger"],
    )

    mock_tools["db"].execute.return_value = 10

    res = await create_plugin.execute({
        "type": "link_filter",
        "action": "ban",
        "target_users": ["@bad_user", "troublemaker"],
        "exempt_users": ["@trusted_one"],
    })

    assert res["success"] is True
    assert res["data"]["id"] == 10
    assert res["data"]["target_users"] == ["bad_user", "troublemaker"]
    assert res["data"]["exempt_users"] == ["trusted_one"]

    # Verify DB query parameters
    sql, params = mock_tools["db"].execute.call_args.args
    assert "target_users" in sql
    assert "exempt_users" in sql
    assert params[5] == "bad_user,troublemaker"
    assert params[6] == "trusted_one"

    # List rules
    mock_tools["db"].query.return_value = [
        {
            "id": 10,
            "type": "link_filter",
            "value": None,
            "action": "ban",
            "duration_s": None,
            "enabled": 1,
            "exempt_roles": "mod",
            "target_users": "bad_user,troublemaker",
            "exempt_users": "trusted_one",
        }
    ]
    list_res = await list_plugin.execute({})
    assert list_res["success"] is True
    rule = list_res["data"][0]
    assert rule["target_users"] == ["bad_user", "troublemaker"]
    assert rule["exempt_users"] == ["trusted_one"]

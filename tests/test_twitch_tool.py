"""
Tests for TwitchTool — no network calls, no Twitch credentials needed.

Covers: availability guard, scope deduplication, event type deduplication,
session management, and require_scopes.

All tests use a fake eventsub/api to avoid real HTTP/WebSocket connections.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from tools.twitch.twitch_tool import TwitchTool


# ─── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def tool():
    """TwitchTool pre-configured as available, with mocked internals."""
    t = TwitchTool()
    t._available = True
    t._client_id = "fake_client_id"
    t._client_secret = "fake_secret"
    t._redirect_uri = "http://localhost/callback"

    # Mock internal clients so no real HTTP/WS calls are made
    fake_eventsub = MagicMock()
    fake_eventsub.register_subscription = MagicMock()
    fake_eventsub.on_event = MagicMock()
    fake_eventsub.connect = AsyncMock()
    fake_eventsub.disconnect = AsyncMock()

    fake_api = MagicMock()
    fake_api.get_auth_url = MagicMock(return_value="https://twitch.tv/oauth?...")
    fake_api.stop = AsyncMock()

    t._eventsub = fake_eventsub
    t._api = fake_api
    return t


@pytest.fixture
def unavailable_tool():
    """TwitchTool that has NOT been set up (no credentials)."""
    t = TwitchTool()
    t._available = False
    return t


# ─── Availability guard ───────────────────────────────────────────────────

class TestAvailabilityGuard:
    def test_register_raises_when_unavailable(self, unavailable_tool):
        with pytest.raises(RuntimeError, match="not available"):
            unavailable_tool.register("stream.online", "1", [])

    def test_require_scopes_raises_when_unavailable(self, unavailable_tool):
        with pytest.raises(RuntimeError, match="not available"):
            unavailable_tool.require_scopes(["user:read:chat"])

    def test_on_event_raises_when_unavailable(self, unavailable_tool):
        with pytest.raises(RuntimeError, match="not available"):
            unavailable_tool.on_event("stream.online", lambda x: x)

    def test_get_auth_url_raises_when_unavailable(self, unavailable_tool):
        with pytest.raises(RuntimeError, match="not available"):
            unavailable_tool.get_auth_url()


# ─── Scope management ─────────────────────────────────────────────────────

class TestScopeManagement:
    def test_register_accumulates_scopes(self, tool):
        tool.register("stream.online", "1", ["scope:a"])
        tool.register("stream.offline", "1", ["scope:b"])

        assert "scope:a" in tool._scopes
        assert "scope:b" in tool._scopes

    def test_duplicate_scopes_are_not_added_twice(self, tool):
        tool.register("stream.online", "1", ["user:read:chat"])
        tool.register("stream.offline", "1", ["user:read:chat"])

        assert tool._scopes.count("user:read:chat") == 1

    def test_require_scopes_adds_without_subscription(self, tool):
        tool.require_scopes(["user:write:chat", "channel:moderate"])

        assert "user:write:chat" in tool._scopes
        assert "channel:moderate" in tool._scopes
        tool._eventsub.register_subscription.assert_not_called()

    def test_require_scopes_deduplicates(self, tool):
        tool.require_scopes(["user:read:chat"])
        tool.require_scopes(["user:read:chat"])

        assert tool._scopes.count("user:read:chat") == 1


# ─── Event type deduplication ─────────────────────────────────────────────

class TestEventTypeDeduplication:
    def test_same_event_registered_twice_creates_one_subscription(self, tool):
        tool.register("stream.online", "1", [])
        tool.register("stream.online", "1", [])

        assert tool._eventsub.register_subscription.call_count == 1

    def test_different_events_each_create_subscription(self, tool):
        tool.register("stream.online", "1", [])
        tool.register("stream.offline", "1", [])

        assert tool._eventsub.register_subscription.call_count == 2

    def test_registered_event_types_tracked(self, tool):
        tool.register("channel.follow", "2", [])
        assert "channel.follow" in tool._registered_event_types


# ─── Session management ───────────────────────────────────────────────────

class TestSessionManagement:
    def test_get_session_returns_none_when_not_connected(self, tool):
        assert tool.get_session() is None

    def test_get_session_returns_data_after_connect(self, tool):
        tool._access_token = "tok123"
        tool._refresh_token = "ref456"
        tool._broadcaster_id = "99999"
        tool._login = "streamer"

        session = tool.get_session()
        assert session == {
            "access_token": "tok123",
            "refresh_token": "ref456",
            "broadcaster_id": "99999",
            "login": "streamer",
        }

    @pytest.mark.anyio
    async def test_connect_stores_session(self, tool):
        await tool.connect("mytoken", "myrefreshtoken", "12345", "mylogin")

        assert tool._access_token == "mytoken"
        assert tool._refresh_token == "myrefreshtoken"
        assert tool._broadcaster_id == "12345"
        assert tool._login == "mylogin"
        tool._eventsub.connect.assert_called_once_with("mytoken", "12345")

    @pytest.mark.anyio
    async def test_disconnect_calls_eventsub(self, tool):
        await tool.disconnect()
        tool._eventsub.disconnect.assert_called_once()


# ─── on_event ─────────────────────────────────────────────────────────────

class TestOnEvent:
    def test_on_event_delegates_to_eventsub(self, tool):
        async def handler(data): pass
        tool.on_event("stream.online", handler)
        tool._eventsub.on_event.assert_called_once_with("stream.online", handler)

    def test_on_event_wildcard_delegates(self, tool):
        async def monitor(data): pass
        tool.on_event("*", monitor)
        tool._eventsub.on_event.assert_called_once_with("*", monitor)

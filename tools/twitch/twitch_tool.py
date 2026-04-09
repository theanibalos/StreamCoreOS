"""
Twitch Tool — Complete Twitch platform wrapper for MicroCoreOS
==============================================================

Single injection point for everything Twitch-related. Internally delegates
to two components:
  - _api.py      → Helix REST API + OAuth (HTTP)
  - _eventsub.py → EventSub WebSocket (events from Twitch, including chat)

Chat is received via EventSub channel.chat.message (not IRC).
Chat messages are sent via Helix POST /chat/messages (not IRC).

LIFECYCLE:
  1. setup()         — reads env vars, initializes internal clients.
  2. Plugins on_boot — call register() and on_event() to declare subscriptions.
  3. Plugin          — calls get_auth_url() → user authenticates on Twitch.
  4. Plugin          — calls connect(access_token, broadcaster_id, login)
                       after OAuth callback stores the token.
  5. Tool internally — connects EventSub WS, creates subscriptions via Helix.
                       Handles reconnection automatically.
  6. shutdown()      — disconnects everything gracefully.

ENV VARS (required):
  TWITCH_CLIENT_ID      — Your Twitch app's client ID.
  TWITCH_CLIENT_SECRET  — Your Twitch app's client secret.
  TWITCH_REDIRECT_URI   — OAuth callback URL (default: http://localhost:8000/auth/twitch/callback).
"""

import os
import secrets

from core.base_tool import BaseTool
from tools.twitch._api import TwitchApiClient
from tools.twitch._eventsub import TwitchEventSubClient


class TwitchTool(BaseTool):

    @property
    def name(self) -> str:
        return "twitch"

    def __init__(self) -> None:
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._redirect_uri: str | None = None
        self._api: TwitchApiClient | None = None
        self._eventsub: TwitchEventSubClient | None = None
        # Accumulated scopes from all register()/require_scopes() calls
        self._scopes: list[str] = []
        # Tracks registered event types to avoid duplicate Twitch subscriptions
        self._registered_event_types: set[str] = set()
        # Pending OAuth states for CSRF validation
        self._pending_states: set[str] = set()
        # Active session — populated on connect()
        self._access_token: str | None = None
        self._broadcaster_id: str | None = None
        self._login: str | None = None
        self._available = False

    async def setup(self) -> None:
        self._client_id = os.getenv("TWITCH_CLIENT_ID")
        self._client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self._redirect_uri = os.getenv(
            "TWITCH_REDIRECT_URI", "http://localhost:8000/auth/twitch/callback"
        )

        if not self._client_id or not self._client_secret:
            print(
                "[TwitchTool] ⚠️  TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET not set. "
                "Tool unavailable until env vars are configured."
            )
            return

        self._api = TwitchApiClient(self._client_id, self._client_secret, self._redirect_uri)
        await self._api.start()

        self._eventsub = TwitchEventSubClient(api=self._api, client_id=self._client_id)

        self._available = True
        print("[TwitchTool] Ready.")

    async def on_boot_complete(self, container) -> None:
        if self._available and not self._access_token:
            url, _ = self.get_auth_url()
            scopes_list = "\n  ".join(self._scopes)
            print(
                f"\n{'='*60}\n"
                f"[TwitchTool] No active session — authentication required.\n"
                f"Scopes requested ({len(self._scopes)}):\n  {scopes_list}\n\n"
                f"Open this URL to authorize:\n{url}\n"
                f"{'='*60}\n"
            )

    async def shutdown(self) -> None:
        if self._eventsub:
            await self._eventsub.disconnect()
        if self._api:
            await self._api.stop()
        print("[TwitchTool] Shutdown complete.")

    # ── Registration API (plugins call these in on_boot) ─────────────

    def register(
        self,
        event_type: str,
        version: str,
        scopes: list[str],
        condition: dict | None = None,
    ) -> None:
        """
        Declare an EventSub subscription and its required OAuth scopes.

        Call this in on_boot() BEFORE connect() is invoked.
        The tool accumulates all scopes across plugins and uses them
        to build the OAuth authorization URL.

        condition: dict with values that may contain '{broadcaster_id}' as
                   a template placeholder, replaced automatically on connect().
                   Defaults to {"broadcaster_user_id": "{broadcaster_id}"}.
        """
        self._check_available()
        for scope in scopes:
            if scope not in self._scopes:
                self._scopes.append(scope)

        # Deduplicate: only one Twitch subscription per event type
        if event_type not in self._registered_event_types:
            self._registered_event_types.add(event_type)
            resolved_condition = condition or {"broadcaster_user_id": "{broadcaster_id}"}
            self._eventsub.register_subscription(event_type, version, resolved_condition)

    def require_scopes(self, scopes: list[str]) -> None:
        """
        Add required OAuth scopes without creating an EventSub subscription.
        Use this for IRC chat scopes (chat:read, chat:edit) or any scope
        that doesn't map to an EventSub event type.
        """
        self._check_available()
        for scope in scopes:
            if scope not in self._scopes:
                self._scopes.append(scope)

    def on_event(self, event_type: str, callback) -> None:
        """
        Register a callback to receive a specific Twitch event.
        Use event_type='*' to receive all events.

        callback signature: async def handler(event_data: dict)
        """
        self._check_available()
        self._eventsub.on_event(event_type, callback)

    # ── OAuth API ────────────────────────────────────────────────────

    def get_auth_url(self) -> tuple[str, str]:
        """
        Build the Twitch OAuth2 authorization URL with all accumulated scopes.
        Returns (auth_url, state). State is stored internally for CSRF validation.
        """
        self._check_available()
        state = secrets.token_urlsafe(16)
        url = self._api.get_auth_url(self._scopes, state)
        self._pending_states.add(state)
        return url, state

    def consume_state(self, state: str) -> bool:
        """Validate and consume a CSRF state. Returns True if valid."""
        if state in self._pending_states:
            self._pending_states.discard(state)
            return True
        return False

    async def exchange_code(self, code: str) -> dict:
        """
        Exchange an OAuth authorization code for tokens.
        Returns: {access_token, refresh_token, scope, expires_in, token_type}
        """
        self._check_available()
        return await self._api.exchange_code(code)

    async def refresh_user_token(self, refresh_token: str) -> dict:
        """
        Refresh a user access token.
        Returns: {access_token, refresh_token, scope, expires_in, token_type}
        """
        self._check_available()
        return await self._api.refresh_user_token(refresh_token)

    async def get_user_info(self, access_token: str) -> dict:
        """Get the authenticated user's Twitch profile."""
        self._check_available()
        return await self._api.get_user_info(access_token)

    # ── Connection API ────────────────────────────────────────────────

    async def connect(
        self, access_token: str, broadcaster_id: str, twitch_login: str
    ) -> None:
        """
        Connect the EventSub WebSocket and the IRC chat.

        Call this after the OAuth callback stores the access token.
        The tool will automatically create all registered subscriptions
        once the EventSub session is established.

        access_token:   User's OAuth access token.
        broadcaster_id: Twitch user ID (numeric string) of the streamer.
        twitch_login:   Twitch login name (lowercase) — used as the IRC nick.
        """
        self._check_available()
        self._access_token = access_token
        self._broadcaster_id = broadcaster_id
        self._login = twitch_login
        await self._eventsub.connect(access_token, broadcaster_id)

    async def disconnect(self) -> None:
        """Disconnect EventSub WebSocket."""
        if self._eventsub:
            await self._eventsub.disconnect()

    def get_session(self) -> dict | None:
        """
        Returns the current active session or None if not connected.
        Keys: access_token, broadcaster_id, login
        """
        if not self._access_token:
            return None
        return {
            "access_token": self._access_token,
            "broadcaster_id": self._broadcaster_id,
            "login": self._login,
        }

    # ── Chat API ─────────────────────────────────────────────────────

    async def send_message(self, channel: str, message: str) -> None:
        """Send a message to a Twitch channel via Helix POST /chat/messages.
        Requires user:write:chat scope. 'channel' is accepted for API
        compatibility but the active session's broadcaster_id is always used.
        """
        self._check_available()
        session = self.get_session()
        if not session:
            raise RuntimeError("No active Twitch session")
        await self._api.post(
            "/chat/messages",
            body={
                "broadcaster_id": session["broadcaster_id"],
                "sender_id": session["broadcaster_id"],
                "message": message,
            },
            user_token=session["access_token"],
        )

    # ── Helix API ────────────────────────────────────────────────────

    async def get(
        self,
        endpoint: str,
        params: dict | None = None,
        user_token: str | None = None,
    ) -> dict:
        """GET request to Helix API. Uses App Token if user_token not provided."""
        self._check_available()
        return await self._api.get(endpoint, params, user_token)

    async def post(
        self,
        endpoint: str,
        body: dict | None = None,
        user_token: str | None = None,
    ) -> dict:
        """POST request to Helix API. Uses App Token if user_token not provided."""
        self._check_available()
        return await self._api.post(endpoint, body, user_token)

    async def delete(
        self,
        endpoint: str,
        params: dict | None = None,
        user_token: str | None = None,
    ) -> dict:
        """DELETE request to Helix API. Uses App Token if user_token not provided."""
        self._check_available()
        return await self._api.delete(endpoint, params, user_token)

    # ── Internal ─────────────────────────────────────────────────────

    def _check_available(self) -> None:
        if not self._available:
            raise RuntimeError(
                "TwitchTool not available. Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET."
            )

    def get_interface_description(self) -> str:
        return """
        Twitch Tool (twitch):
        - PURPOSE: Complete Twitch platform wrapper — OAuth, Helix API, EventSub WebSocket, IRC Chat.
        - ENV VARS: TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_REDIRECT_URI (optional).
        - PATTERN: Register in on_boot() → user authenticates → call connect() → receive events.

        REGISTRATION (call in on_boot, before connect):
          - register(event_type, version, scopes, condition?):
              Declare an EventSub subscription and its required OAuth scopes.
              condition defaults to {"broadcaster_user_id": "{broadcaster_id}"}.
              {broadcaster_id} is replaced automatically when connect() is called.
              Example: twitch.register("channel.follow", "2", ["moderator:read:followers"])
          - on_event(event_type, callback):
              Register a callback for a Twitch event. Use '*' for all events.
              Signature: async def handler(event_data: dict)

        CHAT (via EventSub — not IRC):
          - To receive chat messages: register("channel.chat.message", "1",
              scopes=["user:read:chat"],
              condition={"broadcaster_user_id": "{broadcaster_id}", "user_id": "{broadcaster_id}"})
            then on_event("channel.chat.message", callback)
          - To send chat messages: await send_message(channel, message)
              Requires user:write:chat scope (add via require_scopes).

        OAUTH:
          - get_auth_url() -> tuple[str, str]:
              Returns (url, state). Save state for CSRF validation in the callback.
          - await exchange_code(code) -> dict:
              Exchange OAuth code for tokens: {access_token, refresh_token, scope, expires_in}
          - await refresh_user_token(refresh_token) -> dict:
              Refresh a user token. Returns new {access_token, refresh_token, ...}
          - await get_user_info(access_token) -> dict:
              Get the authenticated user's Twitch profile {id, login, display_name, ...}

        CONNECTION:
          - await connect(access_token, broadcaster_id, twitch_login):
              Connect EventSub WebSocket + IRC chat. Creates all registered subscriptions.
          - await disconnect(): Disconnect everything.

        CHAT:
          - await send_message(channel, message): Send a chat message.

        HELIX API:
          - await get(endpoint, params?, user_token?): GET to Helix.
          - await post(endpoint, body?, user_token?): POST to Helix.
          - await delete(endpoint, params?, user_token?): DELETE to Helix.
        """

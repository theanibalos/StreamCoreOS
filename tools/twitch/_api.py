"""
Twitch Helix API + OAuth client.

Internal module — used only by TwitchTool. Not a plugin or tool by itself.
Handles all HTTP communication with Twitch: OAuth URL building, token
exchange, token refresh, App Access Token caching, and Helix API calls.
"""

import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx


class TwitchApiClient:
    OAUTH_BASE = "https://id.twitch.tv/oauth2"
    HELIX_BASE = "https://api.twitch.tv/helix"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._app_token: str | None = None
        self._app_token_expires: datetime | None = None
        self._http: httpx.AsyncClient | None = None
        # Hook for reactive refresh: async def hook() -> str | None (returns new access_token)
        self.on_auth_fail = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=30.0)

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── OAuth ────────────────────────────────────────────────────────

    def get_auth_url(self, scopes: list[str], state: str) -> str:
        """Build the Twitch OAuth2 authorization URL."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(sorted(set(scopes))),
            "state": state,
        }
        return f"{self.OAUTH_BASE}/authorize?{urllib.parse.urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        """Exchange an authorization code for access + refresh tokens."""
        resp = await self._http.post(
            f"{self.OAUTH_BASE}/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self._redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def refresh_user_token(self, refresh_token: str) -> dict:
        """Refresh a user access token using its refresh token."""
        resp = await self._http.post(
            f"{self.OAUTH_BASE}/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_app_token(self) -> str:
        """Get (or renew) the App Access Token (client_credentials flow). Cached in memory."""
        now = datetime.now(timezone.utc)
        if (
            self._app_token
            and self._app_token_expires
            and now < self._app_token_expires - timedelta(minutes=5)
        ):
            return self._app_token

        resp = await self._http.post(
            f"{self.OAUTH_BASE}/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._app_token = data["access_token"]
        self._app_token_expires = now + timedelta(seconds=data["expires_in"])
        return self._app_token

    async def get_user_info(self, access_token: str) -> dict:
        """Get the authenticated user's info from /users."""
        resp = await self._request("GET", "/users", headers=self._user_headers(access_token))
        data = resp.json()
        return data["data"][0] if data.get("data") else {}

    # ── Helix API ────────────────────────────────────────────────────

    async def get(
        self,
        endpoint: str,
        params: dict | None = None,
        user_token: str | None = None,
    ) -> dict:
        headers = self._user_headers(user_token) if user_token else await self._app_headers()
        resp = await self._request("GET", endpoint, params=params, headers=headers, user_token=user_token)
        return resp.json()

    async def post(
        self,
        endpoint: str,
        body: dict | None = None,
        user_token: str | None = None,
    ) -> dict:
        headers = self._user_headers(user_token) if user_token else await self._app_headers()
        resp = await self._request("POST", endpoint, json=body, headers=headers, user_token=user_token)
        return resp.json() if resp.content else {}

    async def delete(
        self,
        endpoint: str,
        params: dict | None = None,
        user_token: str | None = None,
    ) -> dict:
        headers = self._user_headers(user_token) if user_token else await self._app_headers()
        resp = await self._request("DELETE", endpoint, params=params, headers=headers, user_token=user_token)
        return resp.json() if resp.content else {}

    # ── Internal ─────────────────────────────────────────────────────

    async def _request(self, method: str, endpoint: str, user_token: str | None = None, **kwargs) -> httpx.Response:
        """Internal helper to handle requests with optional reactive refresh on 401."""
        url = f"{self.HELIX_BASE}{endpoint}"
        resp = await self._http.request(method, url, **kwargs)

        if resp.status_code == 401 and user_token and self.on_auth_fail:
            # Token expired? Ask the hook for a new one
            new_token = await self.on_auth_fail()
            if new_token:
                # Update headers and retry once
                kwargs["headers"] = self._user_headers(new_token)
                resp = await self._http.request(method, url, **kwargs)

        resp.raise_for_status()
        return resp

    async def _app_headers(self) -> dict:
        token = await self.get_app_token()
        return {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _user_headers(self, token: str) -> dict:
        return {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

import asyncio
import json
import urllib.error
import urllib.request
from typing import Optional

from pydantic import BaseModel

from core.base_plugin import BasePlugin


class ScopesData(BaseModel):
    connected: bool
    required: Optional[list[str]] = None
    granted: Optional[list[str]] = None
    missing: Optional[list[str]] = None


class ScopesResponse(BaseModel):
    success: bool
    data: Optional[ScopesData] = None
    error: Optional[str] = None


class TwitchScopesPlugin(BasePlugin):
    """GET /auth/twitch/scopes — Compare required vs granted OAuth scopes."""

    def __init__(self, twitch, http, logger):
        self.twitch = twitch
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/auth/twitch/scopes",
            "GET",
            self.execute,
            tags=["Twitch Auth"],
            response_model=ScopesResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            session = self.twitch.get_session()
            if not session:
                return {"success": True, "data": {"connected": False}}

            access_token = session["access_token"]
            required = list(getattr(self.twitch, "_scopes", []))

            loop = asyncio.get_running_loop()
            granted = await loop.run_in_executor(
                None, self._fetch_granted_scopes, access_token
            )

            if granted is None:
                return {"success": True, "data": {"connected": False}}

            missing = [s for s in required if s not in granted]

            return {
                "success": True,
                "data": {
                    "connected": True,
                    "required": required,
                    "granted": granted,
                    "missing": missing,
                },
            }
        except Exception as e:
            self.logger.error(f"[TwitchScopes] {e}")
            return {"success": False, "error": str(e)}

    def _fetch_granted_scopes(self, access_token: str) -> list[str] | None:
        """Calls Twitch validate endpoint synchronously (run in executor). Returns None if token invalid."""
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                return body.get("scopes", [])
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return None
            raise

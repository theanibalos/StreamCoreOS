import secrets
from typing import Optional, Any
from pydantic import BaseModel
from core.base_plugin import BasePlugin

# Persisted in the overlay_feed_token table (migration 003), single row id=1.
# The feed reads the same row on every connect. Both plugins compose through
# the `db` tool — they never import each other.
_UPSERT = (
    "INSERT INTO overlay_feed_token (id, token, updated_at) "
    "VALUES (1, $1, CURRENT_TIMESTAMP) "
    "ON CONFLICT(id) DO UPDATE SET token = excluded.token, updated_at = CURRENT_TIMESTAMP"
)


class TokenResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class OverlayTokenPlugin(BasePlugin):
    """
    GET  /api/overlays/token   — read the channel overlay token (creates one on
                                 first read). This is the token pasted into an
                                 external/AI-built overlay's browser-source URL.
    POST /api/overlays/token   — regenerate it. Rotates ALL overlays of the
                                 channel at once (they must be re-pasted), like
                                 Twitch Alerts' "reset URL".

    Guarded by the active Twitch session: only the logged-in broadcaster can
    read or rotate the token (the app's auth model — see twitch_auth_status).
    The token is opaque, read-only and feed-scoped: it can only open
    /api/overlays/feed and read /api/overlays/manifest. Persisted in the DB so
    it survives restarts (unlike an in-memory value).
    """

    def __init__(self, http, db, twitch, logger):
        self.http = http
        self.db = db
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/token", "GET", self.read,
            tags=["Overlays"], response_model=TokenResponse,
        )
        self.http.add_endpoint(
            "/api/overlays/token", "POST", self.regenerate,
            tags=["Overlays"], response_model=TokenResponse,
        )

    def _guard(self) -> bool:
        return self.twitch.get_session() is not None

    async def read(self, data: dict, context=None):
        if not self._guard():
            return {"success": False, "error": "Not authenticated"}
        try:
            row = await self.db.query_one(
                "SELECT token FROM overlay_feed_token WHERE id = 1", []
            )
            token = row["token"] if row else None
            if not token:
                token = secrets.token_urlsafe(32)
                await self.db.execute(_UPSERT, [token])
            return {"success": True, "data": self._payload(token)}
        except Exception as e:
            self.logger.error(f"[OverlayToken] read: {e}")
            return {"success": False, "error": "Could not read token"}

    async def regenerate(self, data: dict, context=None):
        if not self._guard():
            return {"success": False, "error": "Not authenticated"}
        try:
            token = secrets.token_urlsafe(32)
            await self.db.execute(_UPSERT, [token])
            self.logger.info("[OverlayToken] Channel overlay token regenerated")
            return {"success": True, "data": self._payload(token)}
        except Exception as e:
            self.logger.error(f"[OverlayToken] regenerate: {e}")
            return {"success": False, "error": "Could not regenerate token"}

    def _payload(self, token: str) -> dict:
        return {
            "token": token,
            "feed_url": f"/api/overlays/feed?token={token}",
            "manifest_url": "/api/overlays/manifest",
        }

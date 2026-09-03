from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class SyncBitsData(BaseModel):
    synced: int


class SyncBitsResponse(BaseModel):
    success: bool
    data: Optional[SyncBitsData] = None
    error: Optional[str] = None


class SyncBitsPlugin(BasePlugin):
    """
    POST /bits/sync
    Fetches top-100 all-time bits from /helix/bits/leaderboard and upserts into viewer_bits.
    Requires bits:read scope (or channel:read:subscriptions on some accounts).
    """

    def __init__(self, http, twitch, db, logger):
        self.http = http
        self.twitch = twitch
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/bits/sync", "POST", self.execute,
            tags=["Subscribers"],
            response_model=SyncBitsResponse,
        )

    async def execute(self, data: dict, context=None):
        session = self.twitch.get_session()
        if not session:
            return {"success": False, "error": "No hay sesión de Twitch activa"}

        access_token = session["access_token"]

        try:
            resp = await self.twitch.get(
                "/bits/leaderboard",
                params={"count": 100, "period": "all"},
                user_token=access_token,
            )

            entries = resp.get("data", [])
            for entry in entries:
                await self.db.execute(
                    """INSERT INTO viewer_bits (twitch_id, login, display_name, bits_total)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT(twitch_id) DO UPDATE SET
                           login        = excluded.login,
                           display_name = excluded.display_name,
                           bits_total   = excluded.bits_total""",
                    [
                        entry["user_id"],
                        entry["user_login"],
                        entry["user_name"],
                        entry["score"],
                    ],
                )

            self.logger.info(f"[SyncBits] synced {len(entries)} bit donors")
            return {"success": True, "data": {"synced": len(entries)}}

        except Exception as e:
            self.logger.error(f"[SyncBits] {e}")
            return {"success": False, "error": str(e)}

from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class StreamSessionData(BaseModel):
    id: int
    twitch_stream_id: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    title: Optional[str] = None
    game_name: Optional[str] = None
    peak_viewers: int


class StreamHistoryResponse(BaseModel):
    success: bool
    data: Optional[list[StreamSessionData]] = None
    error: Optional[str] = None


class StreamHistoryPlugin(BasePlugin):
    """
    GET /stream/sessions

    Returns the list of past stream sessions from DB.
    Query params: limit (default 20), offset (default 0).
    """

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream/sessions",
            "GET",
            self.execute,
            tags=["Stream"],
            response_model=StreamHistoryResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            limit = int(data.get("limit", 20))
            offset = int(data.get("offset", 0))

            sessions = await self.db.query(
                """SELECT id, twitch_stream_id, started_at, ended_at,
                          title, game_name, peak_viewers
                   FROM stream_sessions
                   ORDER BY started_at DESC
                   LIMIT $1 OFFSET $2""",
                [limit, offset],
            )
            return {"success": True, "data": sessions}
        except Exception as e:
            self.logger.error(f"[StreamHistory] {e}")
            return {"success": False, "error": str(e)}

from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class StreamInfo(BaseModel):
    online: bool
    started_at: Optional[str] = None
    viewer_count: Optional[int] = None
    follower_count: Optional[int] = None


class TopViewer(BaseModel):
    twitch_id: str
    display_name: str
    points: int


class RecentModAction(BaseModel):
    display_name: str
    action: str
    reason: str
    created_at: str


class DashboardStatsData(BaseModel):
    stream: StreamInfo
    top_viewers: list[TopViewer]
    recent_mod_actions: list[RecentModAction]
    total_viewers: int


class DashboardStatsResponse(BaseModel):
    success: bool
    data: Optional[DashboardStatsData] = None
    error: Optional[str] = None


class DashboardStatsPlugin(BasePlugin):
    """
    GET /dashboard/stats

    Aggregates data from multiple domain tables (single DB, no imports needed)
    and the Helix API into a single response for the dashboard frontend.
    """

    def __init__(self, http, twitch, db, state, logger):
        self.http = http
        self.twitch = twitch
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/dashboard/stats",
            "GET",
            self.execute,
            tags=["Dashboard"],
            response_model=DashboardStatsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            session = self.twitch.get_session()
            broadcaster_id = session["broadcaster_id"] if session else None
            access_token = session["access_token"] if session else None
            online = self.state.get("online", default=False, namespace="stream_state")

            viewer_count = None
            follower_count = None

            if broadcaster_id and access_token:
                try:
                    if online:
                        stream_data = await self.twitch.get(
                            "/streams",
                            params={"user_id": broadcaster_id},
                            user_token=access_token,
                        )
                        streams = stream_data.get("data", [])
                        if streams:
                            viewer_count = streams[0].get("viewer_count", 0)

                    followers_data = await self.twitch.get(
                        "/channels/followers",
                        params={"broadcaster_id": broadcaster_id},
                        user_token=access_token,
                    )
                    follower_count = followers_data.get("total", 0)
                except Exception as e:
                    self.logger.error(f"[DashboardStats] Helix API error: {e}")

            # Top 5 viewers by points
            top_viewers = await self.db.query(
                "SELECT twitch_id, display_name, points FROM viewers ORDER BY points DESC LIMIT 5"
            )

            # Last 5 mod actions
            recent_mod = await self.db.query(
                "SELECT display_name, action, reason, created_at FROM mod_log ORDER BY created_at DESC LIMIT 5"
            )

            # Total known viewers
            count_row = await self.db.query_one("SELECT count(*) as total FROM viewers")
            total_viewers = count_row["total"] if count_row else 0

            return {
                "success": True,
                "data": {
                    "stream": {
                        "online": online,
                        "started_at": self.state.get("started_at", namespace="stream_state"),
                        "viewer_count": viewer_count,
                        "follower_count": follower_count,
                    },
                    "top_viewers": top_viewers,
                    "recent_mod_actions": recent_mod,
                    "total_viewers": total_viewers,
                },
            }
        except Exception as e:
            self.logger.error(f"[DashboardStats] {e}")
            return {"success": False, "error": str(e)}

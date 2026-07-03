import json
from typing import Optional, Any
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class OverlayDataResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class OverlayDataPlugin(BasePlugin):
    """
    GET /overlays/data  (public — used by OBS browser source renderer)

    Returns live aggregated stats for overlay stat/progress_bar widgets:
      subscribers.active_total  — active subscribers from local DB
      bits.total                — all-time bits accumulated locally
      stream.online             — whether the stream is currently live
    """

    def __init__(self, http, db, state, logger):
        self.http = http
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/data", "GET", self.execute,
            tags=["Overlays"],
            response_model=OverlayDataResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            result = {}

            # Stream online state
            result["stream.online"] = await self.state.get(
                "online", default=False, namespace="stream_state"
            )

            # Active subscriber count
            try:
                row = await self.db.query_one(
                    "SELECT COUNT(*) AS n FROM subscribers WHERE is_active=1", []
                )
                result["subscribers.active_total"] = int(row["n"]) if row else 0
            except Exception:
                result["subscribers.active_total"] = 0

            # Latest viewer + follower counts (from channel_stats collector, runs every 5 min)
            try:
                row = await self.db.query_one(
                    "SELECT viewer_count, follower_count FROM channel_stats ORDER BY id DESC LIMIT 1", []
                )
                result["stream.viewer_count"] = int(row["viewer_count"]) if row else 0
                result["followers.total"]      = int(row["follower_count"]) if row else 0
            except Exception:
                result["stream.viewer_count"] = 0
                result["followers.total"]      = 0

            # All-time bits total
            try:
                row = await self.db.query_one(
                    "SELECT COALESCE(SUM(bits_total), 0) AS n FROM viewer_bits", []
                )
                result["bits.total"] = int(row["n"]) if row else 0
            except Exception:
                result["bits.total"] = 0

            # Dynamic vars pool (published via the "overlay.vars.set" bus event)
            try:
                rows = await self.db.query("SELECT key, value FROM overlay_vars", [])
                for r in rows:
                    try:
                        result[r["key"]] = json.loads(r["value"])
                    except (json.JSONDecodeError, TypeError):
                        result[r["key"]] = r["value"]
            except Exception:
                pass

            return {"success": True, "data": result}
        except Exception as e:
            self.logger.error(f"[OverlayData] {e}")
            return {"success": False, "error": str(e)}

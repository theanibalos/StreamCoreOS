import json
from typing import Optional, Any
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class OverlayConfigResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class OverlayConfigPlugin(BasePlugin):
    """
    Public endpoint (no auth) — used by the OBS browser source renderer.
    Returns the overlay config JSON for a given overlay id.
    """
    def __init__(self, http, db, state, logger):
        self.http = http
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/{id}/config", "GET", self.execute,
            tags=["Overlays"],
            response_model=OverlayConfigResponse,
        )

    async def _current_stats(self) -> dict:
        stats: dict = {
            "stream.online":   await self.state.get("online", default=False, namespace="stream_state"),
        }
        try:
            row = await self.db.query_one("SELECT viewer_count, follower_count FROM channel_stats ORDER BY id DESC LIMIT 1", [])
            stats["followers.total"] = int(row["follower_count"]) if row else 0
            stats["stream.viewer_count"] = int(row["viewer_count"]) if row else 0
        except Exception:
            stats["followers.total"] = 0
            stats["stream.viewer_count"] = 0

        try:
            row = await self.db.query_one("SELECT COUNT(*) AS n FROM subscribers WHERE is_active=1", [])
            stats["subscribers.active_total"] = int(row["n"]) if row else 0
        except Exception:
            stats["subscribers.active_total"] = 0

        try:
            row = await self.db.query_one("SELECT COALESCE(SUM(bits_total), 0) AS n FROM viewer_bits", [])
            stats["bits.total"] = int(row["n"]) if row else 0
        except Exception:
            stats["bits.total"] = 0

        # Dynamic vars pool (published via the "overlay.vars.set" bus event)
        try:
            rows = await self.db.query("SELECT key, value FROM overlay_vars", [])
            for r in rows:
                try:
                    stats[r["key"]] = json.loads(r["value"])
                except (json.JSONDecodeError, TypeError):
                    stats[r["key"]] = r["value"]
        except Exception:
            pass
        return stats

    async def execute(self, data: dict, context=None):
        try:
            if context:
                context.set_header("Cache-Control", "no-store, no-cache, must-revalidate, proxy-revalidate")
                context.set_header("Pragma", "no-cache")
                context.set_header("Expires", "0")

            overlay_id = data.get("id")
            try:
                oid = int(overlay_id) if overlay_id is not None else None
            except (ValueError, TypeError):
                oid = overlay_id

            row = await self.db.query_one(
                "SELECT id, name, config FROM overlays WHERE id = $1", [oid]
            )
            if not row:
                return {"success": False, "error": "Overlay not found"}

            row["config"] = json.loads(row["config"])
            # Atomic: include stats in the config response
            row["stats"] = await self._current_stats()
            return {"success": True, "data": row}
        except Exception as e:
            self.logger.error(f"[OverlayConfig] {e}")
            return {"success": False, "error": str(e)}

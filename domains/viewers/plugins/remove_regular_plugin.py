from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class RemoveRegularResponse(BaseModel):
    success: bool
    error: Optional[str] = None


class RemoveRegularPlugin(BasePlugin):
    """DELETE /viewers/regulars/{twitch_id} — Remove a viewer from the regulars list."""

    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/regulars/{twitch_id}", "DELETE", self.execute,
            tags=["Viewers"],
            response_model=RemoveRegularResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            twitch_id = data["twitch_id"]
            viewer = await self.db.query_one(
                "SELECT display_name FROM viewers WHERE twitch_id=$1 AND is_regular=1",
                [twitch_id],
            )
            if not viewer:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Regular not found"}

            await self.db.execute(
                "UPDATE viewers SET is_regular=0 WHERE twitch_id=$1", [twitch_id]
            )
            await self.bus.publish("viewer.regular.removed", {
                "twitch_id": twitch_id,
                "display_name": viewer["display_name"],
            })
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[RemoveRegular] {e}")
            return {"success": False, "error": str(e)}

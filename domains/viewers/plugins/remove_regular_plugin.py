from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class RemoveRegularResponse(BaseModel):
    success: bool
    error: Optional[str] = None


class RemoveRegularPlugin(BasePlugin):
    """DELETE /viewers/regulars/{global_user_id} — Remove a viewer from regulars."""

    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/regulars/{global_user_id}", "DELETE", self.execute,
            tags=["Viewers"],
            response_model=RemoveRegularResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            global_user_id = data["global_user_id"]
            viewer = await self.db.query_one(
                """SELECT global_user_id, platform, platform_user_id, display_name
                   FROM viewers WHERE global_user_id=$1 AND is_regular=1""",
                [global_user_id],
            )
            if not viewer:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Regular not found"}

            await self.db.execute(
                "UPDATE viewers SET is_regular=0 WHERE global_user_id=$1", [global_user_id]
            )
            await self.bus.publish("viewer.regular.removed", dict(viewer))
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[RemoveRegular] {e}")
            return {"success": False, "error": str(e)}

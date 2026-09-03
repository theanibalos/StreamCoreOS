from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class CommandData(BaseModel):
    id: int
    name: str
    response: str
    cooldown_s: int
    global_cooldown_s: int
    userlevel: str
    use_count: int
    enabled: bool
    action: Optional[str] = None


class ListCommandsResponse(BaseModel):
    success: bool
    data: Optional[list[CommandData]] = None
    error: Optional[str] = None


class ListCommandsPlugin(BasePlugin):
    """GET /chat/commands — List all chat commands."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/commands", "GET", self.execute,
            tags=["Chat"],
            response_model=ListCommandsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query(
                "SELECT id, name, response, cooldown_s, global_cooldown_s, userlevel, use_count, enabled, action FROM chat_commands ORDER BY name"
            )
            commands = [
                {**r, "enabled": bool(r["enabled"])} for r in rows
            ]
            return {"success": True, "data": commands}
        except Exception as e:
            self.logger.error(f"[ListCommands] {e}")
            return {"success": False, "error": str(e)}

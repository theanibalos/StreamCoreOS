from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class DeleteCommandResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class DeleteCommandPlugin(BasePlugin):
    """DELETE /chat/commands/{id} — Delete a chat command."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/commands/{id}", "DELETE", self.execute,
            tags=["Chat"],
            response_model=DeleteCommandResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            cmd_id = int(data["id"])
            affected = await self.db.execute(
                "DELETE FROM chat_commands WHERE id=$1", [cmd_id]
            )
            if not affected:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Command not found"}
            return {"success": True, "data": {"id": cmd_id}}
        except Exception as e:
            self.logger.error(f"[DeleteCommand] {e}")
            return {"success": False, "error": str(e)}

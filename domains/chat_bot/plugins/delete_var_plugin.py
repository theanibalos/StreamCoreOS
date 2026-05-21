from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class DeleteVarResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class DeleteVarPlugin(BasePlugin):
    """DELETE /chat/vars/{id} — Delete a stream variable."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/vars/{id}", "DELETE", self.execute,
            tags=["Chat"],
            response_model=DeleteVarResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            var_id = int(data["id"])
            affected = await self.db.execute(
                "DELETE FROM chat_vars WHERE id=$1", [var_id]
            )
            if not affected:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Variable not found"}
            return {"success": True, "data": {"id": var_id}}
        except Exception as e:
            self.logger.error(f"[DeleteVar] {e}")
            return {"success": False, "error": str(e)}

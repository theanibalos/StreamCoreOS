from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class DeleteStreamOutputData(BaseModel):
    id: int
    deleted: bool


class DeleteStreamOutputResponse(BaseModel):
    success: bool
    data: Optional[DeleteStreamOutputData] = None
    error: Optional[str] = None


class DeleteStreamOutputPlugin(BasePlugin):
    """DELETE /stream-outputs/{id} — Delete a stream destination."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/stream-outputs/{id}", "DELETE", self.execute,
            tags=["Stream Outputs"],
            response_model=DeleteStreamOutputResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            output_id = data.get("id")
            affected = await self.db.execute("DELETE FROM stream_outputs WHERE id=$1", [output_id])
            if not affected:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Stream output not found"}
            return {"success": True, "data": {"id": output_id, "deleted": True}}
        except Exception as e:
            self.logger.error(f"[DeleteStreamOutput] {e}")
            return {"success": False, "error": str(e)}

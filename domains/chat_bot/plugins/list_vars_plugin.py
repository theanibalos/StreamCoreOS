from typing import List, Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class VarData(BaseModel):
    id: int
    name: str
    value: str
    enabled: bool


class ListVarsResponse(BaseModel):
    success: bool
    data: Optional[List[VarData]] = None
    error: Optional[str] = None


class ListVarsPlugin(BasePlugin):
    """GET /chat/vars — List all stream variables."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/vars", "GET", self.execute,
            tags=["Chat"],
            response_model=ListVarsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query("SELECT * FROM chat_vars ORDER BY name ASC")
            return {"success": True, "data": [
                {"id": r["id"], "name": r["name"], "value": r["value"], "enabled": bool(r["enabled"])}
                for r in rows
            ]}
        except Exception as e:
            self.logger.error(f"[ListVars] {e}")
            return {"success": False, "error": str(e)}

from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class CreateVarRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    value: str = Field(default="0", max_length=500)


class VarData(BaseModel):
    id: int
    name: str
    value: str
    enabled: bool


class CreateVarResponse(BaseModel):
    success: bool
    data: Optional[VarData] = None
    error: Optional[str] = None


class CreateVarPlugin(BasePlugin):
    """POST /chat/vars — Create a new stream variable."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/vars", "POST", self.execute,
            tags=["Chat"],
            request_model=CreateVarRequest,
            response_model=CreateVarResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateVarRequest(**data)
            var_id = await self.db.execute(
                "INSERT INTO chat_vars (name, value) VALUES ($1, $2) RETURNING id",
                [req.name, req.value],
            )
            return {"success": True, "data": {
                "id": var_id, "name": req.name, "value": req.value, "enabled": True,
            }}
        except Exception as e:
            self.logger.error(f"[CreateVar] {e}")
            return {"success": False, "error": str(e)}

from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class UpdateVarRequest(BaseModel):
    value: Optional[str] = Field(default=None, max_length=500)
    enabled: Optional[bool] = None


class VarData(BaseModel):
    id: int
    name: str
    value: str
    enabled: bool


class UpdateVarResponse(BaseModel):
    success: bool
    data: Optional[VarData] = None
    error: Optional[str] = None


class UpdateVarPlugin(BasePlugin):
    """PUT /chat/vars/{id} — Update a stream variable."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/vars/{id}", "PUT", self.execute,
            tags=["Chat"],
            request_model=UpdateVarRequest,
            response_model=UpdateVarResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            var_id = int(data["id"])
            req = UpdateVarRequest(**{k: v for k, v in data.items() if k != "id"})

            var = await self.db.query_one("SELECT * FROM chat_vars WHERE id=$1", [var_id])
            if not var:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Variable not found"}

            new_value = req.value if req.value is not None else var["value"]
            new_enabled = int(req.enabled) if req.enabled is not None else var["enabled"]

            await self.db.execute(
                "UPDATE chat_vars SET value=$1, enabled=$2 WHERE id=$3",
                [new_value, new_enabled, var_id],
            )
            return {"success": True, "data": {
                "id": var_id, "name": var["name"],
                "value": new_value, "enabled": bool(new_enabled),
            }}
        except Exception as e:
            self.logger.error(f"[UpdateVar] {e}")
            return {"success": False, "error": str(e)}

from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


_USERLEVELS = r"^(everyone|subscriber|vip|regular|moderator|broadcaster)$"


class UpdateCommandRequest(BaseModel):
    response: Optional[str] = Field(default=None, min_length=1, max_length=500)
    cooldown_s: Optional[int] = Field(default=None, ge=0, le=3600)
    global_cooldown_s: Optional[int] = Field(default=None, ge=0, le=3600)
    userlevel: Optional[str] = Field(default=None, pattern=_USERLEVELS)
    enabled: Optional[bool] = None


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


class UpdateCommandResponse(BaseModel):
    success: bool
    data: Optional[CommandData] = None
    error: Optional[str] = None


class UpdateCommandPlugin(BasePlugin):
    """PUT /chat/commands/{id} — Update a chat command."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/commands/{id}", "PUT", self.execute,
            tags=["Chat"],
            request_model=UpdateCommandRequest,
            response_model=UpdateCommandResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            cmd_id = int(data["id"])
            req = UpdateCommandRequest(**{k: v for k, v in data.items() if k != "id"})

            cmd = await self.db.query_one(
                "SELECT * FROM chat_commands WHERE id=$1", [cmd_id]
            )
            if not cmd:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Command not found"}

            new_response = req.response if req.response is not None else cmd["response"]
            new_cooldown = req.cooldown_s if req.cooldown_s is not None else cmd["cooldown_s"]
            new_global_cooldown = req.global_cooldown_s if req.global_cooldown_s is not None else cmd["global_cooldown_s"]
            new_userlevel = req.userlevel if req.userlevel is not None else cmd["userlevel"]
            new_enabled = int(req.enabled) if req.enabled is not None else cmd["enabled"]

            await self.db.execute(
                """UPDATE chat_commands
                   SET response=$1, cooldown_s=$2, global_cooldown_s=$3, userlevel=$4, enabled=$5
                   WHERE id=$6""",
                [new_response, new_cooldown, new_global_cooldown, new_userlevel, new_enabled, cmd_id],
            )
            return {"success": True, "data": {
                "id": cmd_id, "name": cmd["name"],
                "response": new_response, "cooldown_s": new_cooldown,
                "global_cooldown_s": new_global_cooldown, "userlevel": new_userlevel,
                "use_count": cmd["use_count"], "enabled": bool(new_enabled),
                "action": cmd["action"],
            }}
        except Exception as e:
            self.logger.error(f"[UpdateCommand] {e}")
            return {"success": False, "error": str(e)}

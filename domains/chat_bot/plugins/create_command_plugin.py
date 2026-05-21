from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


_USERLEVELS = r"^(everyone|subscriber|vip|regular|moderator|broadcaster)$"


class CreateCommandRequest(BaseModel):
    name: str = Field(min_length=2, max_length=50, pattern=r"^![a-z0-9_]+$")
    response: str = Field(min_length=1, max_length=500)
    cooldown_s: int = Field(default=30, ge=0, le=3600)
    global_cooldown_s: int = Field(default=0, ge=0, le=3600)
    userlevel: str = Field(default="everyone", pattern=_USERLEVELS)


class CommandData(BaseModel):
    id: int
    name: str
    response: str
    cooldown_s: int
    global_cooldown_s: int
    userlevel: str
    use_count: int
    enabled: bool


class CreateCommandResponse(BaseModel):
    success: bool
    data: Optional[CommandData] = None
    error: Optional[str] = None


class CreateCommandPlugin(BasePlugin):
    """POST /chat/commands — Create a new chat command."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/commands", "POST", self.execute,
            tags=["Chat"],
            request_model=CreateCommandRequest,
            response_model=CreateCommandResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateCommandRequest(**data)
            cmd_id = await self.db.execute(
                """INSERT INTO chat_commands (name, response, cooldown_s, global_cooldown_s, userlevel)
                   VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                [req.name, req.response, req.cooldown_s, req.global_cooldown_s, req.userlevel],
            )
            return {"success": True, "data": {
                "id": cmd_id, "name": req.name, "response": req.response,
                "cooldown_s": req.cooldown_s, "global_cooldown_s": req.global_cooldown_s,
                "userlevel": req.userlevel, "use_count": 0, "enabled": True,
            }}
        except Exception as e:
            self.logger.error(f"[CreateCommand] {e}")
            return {"success": False, "error": str(e)}

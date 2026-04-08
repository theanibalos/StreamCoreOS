from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin

VALID_TYPES = {"word_filter", "link_filter", "caps_filter", "spam_filter", "ai_filter"}
VALID_ACTIONS = {"timeout", "ban", "delete"}


class CreateModRuleRequest(BaseModel):
    type: str = Field(min_length=1)
    value: Optional[str] = Field(default=None, max_length=4000)
    action: str = Field(default="timeout")
    duration_s: Optional[int] = Field(default=600, ge=1, le=1209600)


class ModRuleData(BaseModel):
    id: int
    type: str
    value: Optional[str] = None
    action: str
    duration_s: Optional[int] = None
    enabled: bool


class CreateModRuleResponse(BaseModel):
    success: bool
    data: Optional[ModRuleData] = None
    error: Optional[str] = None


class CreateModRulePlugin(BasePlugin):
    """POST /moderation/rules — Create a new moderation rule."""

    def __init__(self, http, event_bus, db, logger):
        self.http = http
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/moderation/rules", "POST", self.execute,
            tags=["Moderation"],
            request_model=CreateModRuleRequest,
            response_model=CreateModRuleResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = CreateModRuleRequest(**data)
            if req.type not in VALID_TYPES:
                return {"success": False, "error": f"Invalid type. Must be one of: {VALID_TYPES}"}
            if req.action not in VALID_ACTIONS:
                return {"success": False, "error": f"Invalid action. Must be one of: {VALID_ACTIONS}"}

            rule_id = await self.db.execute(
                """INSERT INTO mod_rules (type, value, action, duration_s)
                   VALUES ($1,$2,$3,$4) RETURNING id""",
                [req.type, req.value, req.action, req.duration_s],
            )
            await self.bus.publish("moderation.rules.updated", {"rule_id": rule_id})
            return {"success": True, "data": {
                "id": rule_id, "type": req.type, "value": req.value,
                "action": req.action, "duration_s": req.duration_s, "enabled": True,
            }}
        except Exception as e:
            self.logger.error(f"[CreateModRule] {e}")
            return {"success": False, "error": str(e)}

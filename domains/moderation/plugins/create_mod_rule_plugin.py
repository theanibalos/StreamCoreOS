from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin

VALID_TYPES = {"word_filter", "link_filter", "caps_filter", "spam_filter", "ai_filter"}
VALID_ACTIONS = {"timeout", "ban", "delete"}
VALID_ROLES = {"mod", "vip", "regular", "sub"}


class CreateModRuleRequest(BaseModel):
    type: str = Field(min_length=1)
    value: Optional[str] = Field(default=None, max_length=4000)
    action: str = Field(default="timeout")
    duration_s: Optional[int] = Field(default=600, ge=1, le=1209600)
    exempt_roles: list[str] = Field(default_factory=list)


class ModRuleData(BaseModel):
    id: int
    type: str
    value: Optional[str] = None
    action: str
    duration_s: Optional[int] = None
    enabled: bool
    exempt_roles: list[str] = []


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
            "/api/moderation/rules", "POST", self.execute,
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
            invalid_roles = set(req.exempt_roles) - VALID_ROLES
            if invalid_roles:
                return {"success": False, "error": f"Invalid exempt_roles: {invalid_roles}. Must be one of: {VALID_ROLES}"}

            exempt_roles_str = ",".join(req.exempt_roles)
            rule_id = await self.db.execute(
                """INSERT INTO mod_rules (type, value, action, duration_s, exempt_roles)
                   VALUES ($1,$2,$3,$4,$5) RETURNING id""",
                [req.type, req.value, req.action, req.duration_s, exempt_roles_str],
            )
            await self.bus.publish("moderation.rules.updated", {"rule_id": rule_id})
            return {"success": True, "data": {
                "id": rule_id, "type": req.type, "value": req.value,
                "action": req.action, "duration_s": req.duration_s, "enabled": True,
                "exempt_roles": req.exempt_roles,
            }}
        except Exception as e:
            self.logger.error(f"[CreateModRule] {e}")
            return {"success": False, "error": str(e)}

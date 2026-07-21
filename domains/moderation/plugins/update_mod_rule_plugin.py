from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin

VALID_ROLES = {"mod", "vip", "regular", "sub"}


class UpdateModRuleRequest(BaseModel):
    value: Optional[str] = Field(default=None, max_length=2000)
    action: Optional[str] = None
    duration_s: Optional[int] = Field(default=None, ge=1, le=1209600)
    enabled: Optional[bool] = None
    exempt_roles: Optional[list[str]] = None


class ModRuleData(BaseModel):
    id: int
    type: str
    value: Optional[str] = None
    action: str
    duration_s: Optional[int] = None
    enabled: bool
    exempt_roles: list[str] = []


class UpdateModRuleResponse(BaseModel):
    success: bool
    data: Optional[ModRuleData] = None
    error: Optional[str] = None


class UpdateModRulePlugin(BasePlugin):
    """PUT /moderation/rules/{id} — Update a moderation rule."""

    def __init__(self, http, event_bus, db, logger):
        self.http = http
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/moderation/rules/{id}", "PUT", self.execute,
            tags=["Moderation"],
            request_model=UpdateModRuleRequest,
            response_model=UpdateModRuleResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rule_id = int(data["id"])
            req = UpdateModRuleRequest(**{k: v for k, v in data.items() if k != "id"})

            rule = await self.db.query_one("SELECT * FROM mod_rules WHERE id=$1", [rule_id])
            if not rule:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Rule not found"}

            if req.exempt_roles is not None:
                invalid_roles = set(req.exempt_roles) - VALID_ROLES
                if invalid_roles:
                    return {"success": False, "error": f"Invalid exempt_roles: {invalid_roles}. Must be one of: {VALID_ROLES}"}

            new_value = req.value if req.value is not None else rule["value"]
            new_action = req.action if req.action is not None else rule["action"]
            new_duration = req.duration_s if req.duration_s is not None else rule["duration_s"]
            new_enabled = int(req.enabled) if req.enabled is not None else rule["enabled"]
            new_exempt_roles = (
                ",".join(req.exempt_roles) if req.exempt_roles is not None else (rule["exempt_roles"] or "")
            )

            await self.db.execute(
                "UPDATE mod_rules SET value=$1, action=$2, duration_s=$3, enabled=$4, exempt_roles=$5 WHERE id=$6",
                [new_value, new_action, new_duration, new_enabled, new_exempt_roles, rule_id],
            )
            await self.bus.publish("moderation.rules.updated", {"rule_id": rule_id})
            return {"success": True, "data": {
                "id": rule_id, "type": rule["type"], "value": new_value,
                "action": new_action, "duration_s": new_duration, "enabled": bool(new_enabled),
                "exempt_roles": [x for x in new_exempt_roles.split(",") if x],
            }}
        except Exception as e:
            self.logger.error(f"[UpdateModRule] {e}")
            return {"success": False, "error": str(e)}

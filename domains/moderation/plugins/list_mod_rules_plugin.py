from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ModRuleData(BaseModel):
    id: int
    type: str
    value: Optional[str] = None
    action: str
    duration_s: Optional[int] = None
    enabled: bool
    exempt_roles: list[str] = []


class ListModRulesResponse(BaseModel):
    success: bool
    data: Optional[list[ModRuleData]] = None
    error: Optional[str] = None


class ListModRulesPlugin(BasePlugin):
    """GET /moderation/rules — List all moderation rules."""

    def __init__(self, http, db, logger):
        self.http = http
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/moderation/rules", "GET", self.execute,
            tags=["Moderation"],
            response_model=ListModRulesResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rows = await self.db.query(
                "SELECT id, type, value, action, duration_s, enabled, exempt_roles FROM mod_rules ORDER BY id"
            )
            rules = [
                {
                    **r,
                    "enabled": bool(r["enabled"]),
                    "exempt_roles": [x for x in (r["exempt_roles"] or "").split(",") if x],
                }
                for r in rows
            ]
            return {"success": True, "data": rules}
        except Exception as e:
            self.logger.error(f"[ListModRules] {e}")
            return {"success": False, "error": str(e)}

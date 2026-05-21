from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class DeleteModRuleResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class DeleteModRulePlugin(BasePlugin):
    """DELETE /moderation/rules/{id} — Delete a moderation rule."""

    def __init__(self, http, event_bus, db, logger):
        self.http = http
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/moderation/rules/{id}", "DELETE", self.execute,
            tags=["Moderation"],
            response_model=DeleteModRuleResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            rule_id = int(data["id"])
            affected = await self.db.execute(
                "DELETE FROM mod_rules WHERE id=$1", [rule_id]
            )
            if not affected:
                if context:
                    context.set_status(404)
                return {"success": False, "error": "Rule not found"}
            await self.bus.publish("moderation.rules.updated", {"rule_id": rule_id})
            return {"success": True, "data": {"id": rule_id}}
        except Exception as e:
            self.logger.error(f"[DeleteModRule] {e}")
            return {"success": False, "error": str(e)}

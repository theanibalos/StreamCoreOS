import asyncio
from core.base_plugin import BasePlugin

_NS = "ai_mod_rules"


class AiModPlugin(BasePlugin):
    """
    Evaluates active ai_filter rules against every incoming chat message.

    Each rule's `value` field is the system prompt describing what to detect.
    The AI responds TRUE/FALSE — on TRUE the configured action is enforced.

    Completely independent from AutoModPlugin. Delete this file to disable
    AI moderation without affecting any other moderation feature.
    """

    def __init__(self, twitch, event_bus, db, state, ai, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.db = db
        self.state = state
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.twitch.require_scopes([
            "moderator:manage:banned_users",
            "moderator:manage:chat_messages",
        ])
        await self.bus.subscribe("chat.message.received", self._on_message)
        await self.bus.subscribe("moderation.rules.updated", self._invalidate_cache)
        await self._load_rules()

    async def _load_rules(self):
        try:
            rules = await self.db.query(
                "SELECT * FROM mod_rules WHERE enabled=1 AND type='ai_filter'"
            )
            self.state.set("rules", rules, namespace=_NS)
        except Exception as e:
            self.logger.error(f"[AiMod] Failed to load rules: {e}")

    async def _invalidate_cache(self, _data: dict):
        await self._load_rules()

    async def _on_message(self, msg: dict):
        if msg.get("is_broadcaster") or msg.get("is_mod"):
            return

        if not self.ai.is_configured():
            return

        rules = self.state.get("rules", default=[], namespace=_NS)
        if not rules:
            return

        message = msg.get("message", "")
        user_id = msg.get("user_id", "")
        display_name = msg.get("display_name", "")
        message_id = msg.get("message_id", "")

        for rule in rules:
            if await self._evaluate(rule, message):
                await self._enforce(rule, user_id, display_name, message, message_id)
                break

    async def _evaluate(self, rule: dict, message: str) -> bool:
        system_prompt = rule.get("value") or "Detect harmful, toxic, or rule-breaking messages."
        try:
            response = await self.ai.complete(
                messages=[{"role": "user", "content": f"Message: {message}"}],
                system=f"{system_prompt}\nRespond ONLY with TRUE or FALSE. No explanations.",
                max_tokens=5,
                temperature=0.0,
            )
            return response.strip().upper() == "TRUE"
        except Exception as e:
            self.logger.error(f"[AiMod] Evaluation failed for rule #{rule['id']}: {e}")
            return False

    async def _enforce(self, rule: dict, user_id: str, display_name: str, message: str, message_id: str):
        action = rule["action"]
        session = self.twitch.get_session()
        if not session:
            return

        broadcaster_id = session["broadcaster_id"]
        access_token = session["access_token"]
        reason = f"AI-Mod: rule #{rule['id']}"

        try:
            if action == "ban":
                await self.twitch.post(
                    f"/moderation/bans?broadcaster_id={broadcaster_id}&moderator_id={broadcaster_id}",
                    body={"data": {"user_id": user_id, "reason": reason}},
                    user_token=access_token,
                )
            elif action == "timeout":
                duration = rule.get("duration_s") or 600
                await self.twitch.post(
                    f"/moderation/bans?broadcaster_id={broadcaster_id}&moderator_id={broadcaster_id}",
                    body={"data": {"user_id": user_id, "duration": duration, "reason": reason}},
                    user_token=access_token,
                )
            elif action == "delete" and message_id:
                await self.twitch.delete(
                    "/moderation/chat",
                    params={
                        "broadcaster_id": broadcaster_id,
                        "moderator_id": broadcaster_id,
                        "message_id": message_id,
                    },
                    user_token=access_token,
                )
        except Exception as e:
            self.logger.error(f"[AiMod] Helix API call failed for {action} on {display_name}: {e}")

        try:
            await self.db.execute(
                "INSERT INTO mod_log (twitch_id, display_name, action, reason, rule_id) VALUES ($1,$2,$3,$4,$5)",
                [user_id, display_name, action, reason, rule["id"]],
            )
            await self.bus.publish("moderation.action.taken", {
                "twitch_id": user_id,
                "display_name": display_name,
                "action": action,
                "reason": reason,
                "rule_id": rule["id"],
            })
        except Exception as e:
            self.logger.error(f"[AiMod] Failed to log action: {e}")

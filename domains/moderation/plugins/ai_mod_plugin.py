from core.base_plugin import BasePlugin

_NS = "ai_mod_rules"
_REGULARS_NS = "ai_mod_regulars"


class AiModPlugin(BasePlugin):
    """
    Evaluates active ai_filter rules against every incoming chat message.

    Each rule's `value` field is the system prompt describing what to detect.
    Uses complete_json() — the model responds with {"flagged": true|false, "reason": "..."}.
    On TRUE the configured action is enforced.

    Independent from AutoModPlugin. Delete this file to disable AI moderation
    without affecting any other feature.
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
        await self.bus.subscribe("viewer.regular.added", self._invalidate_regulars)
        await self.bus.subscribe("viewer.regular.removed", self._invalidate_regulars)
        await self._load_rules()
        await self._load_regulars()

    async def _load_rules(self):
        try:
            rules = await self.db.query(
                "SELECT * FROM mod_rules WHERE enabled=1 AND type='ai_filter'"
            )
            await self.state.set("rules", rules, namespace=_NS)
        except Exception as e:
            self.logger.error(f"[AiMod] Failed to load rules: {e}")

    async def _load_regulars(self):
        try:
            rows = await self.db.query("SELECT twitch_id FROM viewers WHERE is_regular=1")
            regulars = {r["twitch_id"] for r in rows}
            await self.state.set("regulars", regulars, namespace=_REGULARS_NS)
        except Exception as e:
            self.logger.error(f"[AiMod] Failed to load regulars: {e}")

    async def _invalidate_cache(self, event):
        await self._load_rules()

    async def _invalidate_regulars(self, event):
        await self._load_regulars()

    async def _on_message(self, event):
        msg = event.payload
        if msg.get("is_broadcaster"):
            return

        if not self.ai.is_configured():
            return

        regulars = await self.state.get("regulars", default=set(), namespace=_REGULARS_NS)
        sender_roles = set()
        if msg.get("is_mod"):
            sender_roles.add("mod")
        if msg.get("is_vip"):
            sender_roles.add("vip")
        if msg.get("is_sub"):
            sender_roles.add("sub")
        if msg.get("user_id", "") in regulars:
            sender_roles.add("regular")

        rules = await self.state.get("rules", default=[], namespace=_NS)
        if not rules:
            return

        message      = msg.get("message", "")
        user_id      = msg.get("user_id", "")
        display_name = msg.get("display_name", "")
        message_id   = msg.get("message_id", "")

        for rule in rules:
            exempt_roles = {r for r in (rule.get("exempt_roles") or "").split(",") if r}
            if sender_roles & exempt_roles:
                continue

            if await self._evaluate(rule, message):
                await self._enforce(rule, user_id, display_name, message, message_id)
                break

    async def _evaluate(self, rule: dict, message: str) -> bool:
        rule_prompt = (
            rule.get("value")
            or "Detect harmful, toxic, or rule-breaking messages."
        )
        system = (
            f"{rule_prompt}\n"
            'Respond ONLY with a JSON object: {"flagged": true, "reason": "brief explanation"} '
            'or {"flagged": false}.'
        )
        try:
            result = await self.ai.complete_json(
                messages=[{"role": "user", "content": f"Message: {message}"}],
                system=system,
                max_tokens=80,
                temperature=0.0,
            )
            flagged = bool(result.get("flagged", False))
            reason  = result.get("reason", "")
            self.logger.info(
                f"[AiMod] Rule #{rule['id']} → flagged={flagged}"
                + (f" reason={reason!r}" if reason else "")
            )
            return flagged
        except Exception as e:
            code = getattr(e, "code", None)
            if code == "not_configured":
                return False
            self.logger.error(f"[AiMod] Rule #{rule['id']} [{code}]: {e}")
            return False

    async def _enforce(
        self,
        rule: dict,
        user_id: str,
        display_name: str,
        message: str,
        message_id: str,
    ):
        action  = rule["action"]
        session = self.twitch.get_session()
        if not session:
            return

        broadcaster_id = session["broadcaster_id"]
        access_token   = session["access_token"]
        reason         = f"AI-Mod: rule #{rule['id']}"

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
                        "moderator_id":   broadcaster_id,
                        "message_id":     message_id,
                    },
                    user_token=access_token,
                )
        except Exception as e:
            self.logger.error(
                f"[AiMod] Helix API failed for {action} on {display_name}: {e}"
            )

        try:
            await self.db.execute(
                "INSERT INTO mod_log (twitch_id, display_name, action, reason, rule_id) "
                "VALUES ($1,$2,$3,$4,$5)",
                [user_id, display_name, action, reason, rule["id"]],
            )
            await self.bus.publish("moderation.action.taken", {
                "twitch_id":    user_id,
                "display_name": display_name,
                "action":       action,
                "reason":       reason,
                "rule_id":      rule["id"],
            })
        except Exception as e:
            self.logger.error(f"[AiMod] Failed to log action: {e}")

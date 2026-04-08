import re
from core.base_plugin import BasePlugin

# Cache namespace for rules loaded from DB
_NS = "moderation_rules"


class AutoModPlugin(BasePlugin):
    """
    Evaluates all active mod rules against every incoming chat message.

    Rules are cached in the state tool to avoid a DB query per message.
    The cache is invalidated via the moderation.rules.updated event when
    rules are created, updated, or deleted.

    Supported rule types:
      - word_filter   : message contains the word (case-insensitive)
      - link_filter   : message contains a URL
      - caps_filter   : message is >70% uppercase and >10 chars
      - spam_filter   : message contains repeated characters (e.g. "aaaaa")

    Actions: timeout (duration_s), ban, delete
    """

    def __init__(self, twitch, event_bus, db, state, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        # Added moderator:manage:chat_messages for deletion support
        self.twitch.require_scopes([
            "moderator:manage:banned_users",
            "moderator:manage:chat_messages"
        ])
        await self.bus.subscribe("chat.message.received", self._on_message)
        await self.bus.subscribe("moderation.rules.updated", self._invalidate_cache)
        await self._load_rules()

    async def _load_rules(self):
        try:
            rules = await self.db.query(
                "SELECT * FROM mod_rules WHERE enabled=1 AND type != 'ai_filter'"
            )
            self.state.set("rules", rules, namespace=_NS)
        except Exception as e:
            self.logger.error(f"[AutoMod] Failed to load rules: {e}")

    async def _invalidate_cache(self, data: dict):
        await self._load_rules()

    async def _on_message(self, msg: dict):
        if msg.get("is_broadcaster") or msg.get("is_mod"):
            return  # never moderate mods or broadcaster

        rules = self.state.get("rules", default=[], namespace=_NS)
        message = msg.get("message", "")
        user_id = msg.get("user_id", "")
        display_name = msg.get("display_name", "")
        message_id = msg.get("message_id", "")

        for rule in rules:
            if self._matches(rule, message):
                await self._enforce(rule, user_id, display_name, message, message_id)
                break  # apply first matching rule only

    def _matches(self, rule: dict, message: str) -> bool:
        rtype = rule["type"]
        value = rule.get("value", "") or ""

        if rtype == "word_filter":
            words = [w.strip() for w in value.split(",") if w.strip()]
            return any(
                re.search(re.escape(w), message, re.IGNORECASE) for w in words
            )

        if rtype == "link_filter":
            return bool(re.search(r"https?://\S+|www\.\S+", message, re.IGNORECASE))

        if rtype == "caps_filter":
            if len(message) < 10:
                return False
            caps = sum(1 for c in message if c.isupper())
            return (caps / len(message)) > 0.7

        if rtype == "spam_filter":
            # Detect 5+ consecutive identical characters
            return bool(re.search(r"(.)\1{4,}", message))

        return False

    async def _enforce(self, rule: dict, user_id: str, display_name: str, message: str, message_id: str):
        action = rule["action"]
        session = self.twitch.get_session()
        if not session:
            return
        broadcaster_id = session["broadcaster_id"]
        access_token = session["access_token"]
        reason = f"Auto-mod: {rule['type']} rule #{rule['id']}"

        try:
            if action == "ban" and broadcaster_id and access_token:
                endpoint = f"/moderation/bans?broadcaster_id={broadcaster_id}&moderator_id={broadcaster_id}"
                await self.twitch.post(
                    endpoint,
                    body={"data": {"user_id": user_id, "reason": reason}},
                    user_token=access_token,
                )
            elif action == "timeout" and broadcaster_id and access_token:
                endpoint = f"/moderation/bans?broadcaster_id={broadcaster_id}&moderator_id={broadcaster_id}"
                duration = rule.get("duration_s") or 600
                await self.twitch.post(
                    endpoint,
                    body={"data": {"user_id": user_id, "duration": duration, "reason": reason}},
                    user_token=access_token,
                )
            elif action == "delete" and broadcaster_id and access_token and message_id:
                # DELETE /moderation/chat?broadcaster_id=<ID>&moderator_id=<ID>&message_id=<ID>
                endpoint = "/moderation/chat"
                params = {
                    "broadcaster_id": broadcaster_id,
                    "moderator_id": broadcaster_id,
                    "message_id": message_id
                }
                await self.twitch.delete(
                    endpoint,
                    params=params,
                    user_token=access_token
                )
        except Exception as e:
            self.logger.error(f"[AutoMod] Helix API call failed for {action} on {display_name}: {e}")

        # Always log the action
        try:
            await self.db.execute(
                """INSERT INTO mod_log (twitch_id, display_name, action, reason, rule_id)
                   VALUES ($1,$2,$3,$4,$5)""",
                [user_id, display_name, action, reason, rule["id"]],
            )
            await self.bus.publish("moderation.action.taken", {
                "twitch_id": user_id, "display_name": display_name,
                "action": action, "reason": reason, "rule_id": rule["id"],
            })
        except Exception as e:
            self.logger.error(f"[AutoMod] Failed to log action: {e}")

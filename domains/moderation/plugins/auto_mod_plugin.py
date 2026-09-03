import re
from microcoreos.base_plugin import BasePlugin

# Cache namespaces for data loaded from DB
_NS = "moderation_rules"
_REGULARS_NS = "moderation_regulars"


class AutoModPlugin(BasePlugin):
    """
    Evaluates all active mod rules against every incoming chat message.

    Rules are cached in the state tool to avoid a DB query per message.
    The cache is invalidated via the moderation.rules.updated event when
    rules are created, updated, or deleted.

    Broadcaster is always exempt. Mods, VIPs, subs, and regulars
    (viewers.is_regular) are exempt per-rule via each rule's exempt_roles
    (comma-separated: mod, vip, sub, regular) — configurable in the UI.

    Supported rule types:
      - word_filter   : message contains the word (case-insensitive)
      - link_filter   : message contains a URL
      - caps_filter   : message is >70% uppercase and >10 chars
      - spam_filter   : message contains repeated characters (e.g. "aaaaa")

    Actions: timeout (duration_s), ban, delete
    """

    def __init__(self, event_bus, db, state, logger):
        self.bus = event_bus
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self._on_message)
        await self.bus.subscribe("moderation.rules.updated", self._invalidate_cache)
        await self.bus.subscribe("viewer.regular.added", self._invalidate_regulars)
        await self.bus.subscribe("viewer.regular.removed", self._invalidate_regulars)
        await self._load_rules()
        await self._load_regulars()

    async def _load_rules(self):
        try:
            rules = await self.db.query(
                "SELECT * FROM mod_rules WHERE enabled=1 AND type != 'ai_filter'"
            )
            await self.state.set("rules", rules, namespace=_NS)
        except Exception as e:
            self.logger.error(f"[AutoMod] Failed to load rules: {e}")

    async def _load_regulars(self):
        try:
            rows = await self.db.query("SELECT global_user_id FROM viewers WHERE is_regular=1")
            regulars = {r["global_user_id"] for r in rows}
            await self.state.set("regulars", regulars, namespace=_REGULARS_NS)
        except Exception as e:
            self.logger.error(f"[AutoMod] Failed to load regulars: {e}")

    async def _invalidate_cache(self, event):
        await self._load_rules()

    async def _invalidate_regulars(self, event):
        await self._load_regulars()

    async def _on_message(self, event):
        msg = event.payload
        roles = msg.get("roles") or {}
        user = msg.get("user") or {}
        if roles.get("broadcaster"):
            return  # never moderate the broadcaster

        regulars = await self.state.get("regulars", default=set(), namespace=_REGULARS_NS)
        sender_roles = set()
        if roles.get("moderator"):
            sender_roles.add("mod")
        if roles.get("vip"):
            sender_roles.add("vip")
        if roles.get("subscriber"):
            sender_roles.add("sub")
        if user.get("id", "") in regulars:
            sender_roles.add("regular")

        rules = await self.state.get("rules", default=[], namespace=_NS)
        message = msg.get("message", "")
        user_id = user.get("platform_id", "")
        display_name = user.get("display_name", "")
        message_id = msg.get("message_id", "")

        for rule in rules:
            exempt_roles = {r for r in (rule.get("exempt_roles") or "").split(",") if r}
            if sender_roles & exempt_roles:
                continue  # this rule doesn't apply to this sender's roles

            if self._matches(rule, message):
                await self.bus.publish("moderation.action.requested", {
                    "platform": msg.get("platform", "twitch"),
                    "channel_id": msg.get("channel_id"),
                    "message_id": message_id,
                    "user": {
                        "id": user.get("id", ""),
                        "platform_id": user_id,
                        "display_name": display_name,
                    },
                    "action": rule["action"],
                    "duration_s": rule.get("duration_s"),
                    "reason": f"Auto-mod: {rule['type']} rule #{rule['id']}",
                    "rule_id": rule["id"],
                })
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

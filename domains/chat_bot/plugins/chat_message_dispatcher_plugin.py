from datetime import datetime, timezone
from core.base_plugin import BasePlugin


class ChatMessageDispatcherPlugin(BasePlugin):
    """
    Bridge between Twitch EventSub chat events and the internal event_bus.

    Subscribes to channel.chat.message via EventSub WebSocket (replaces IRC).
    Translates incoming messages into events that other domains can consume:
      - chat.message.received  → every message (used by loyalty, moderation, dashboard)
      - chat.command.received  → messages starting with '!' (used by command handler)

    Also logs all messages to the chat_log table.

    Scopes required:
      - user:read:chat   — receive chat messages via EventSub
      - user:write:chat  — send messages via Helix POST /chat/messages
    """

    def __init__(self, twitch, event_bus, db, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.twitch.require_scopes(["user:write:chat"])
        self.twitch.register(
            "channel.chat.message", "1",
            scopes=["user:read:chat"],
            condition={
                "broadcaster_user_id": "{broadcaster_id}",
                "user_id": "{broadcaster_id}",
            },
        )
        self.twitch.on_event("channel.chat.message", self._on_message)

    async def _on_message(self, event: dict):
        badges_list = event.get("badges", [])
        badge_ids = {b.get("set_id", "") for b in badges_list}

        raw_message = event.get("message", {})
        fragments = [
            {
                "type": f.get("type", "text"),
                "text": f.get("text", ""),
                "emote_id": f.get("emote", {}).get("id") if f.get("type") == "emote" else None,
                "emote_animated": "animated" in f.get("emote", {}).get("format", []) if f.get("type") == "emote" else False,
            }
            for f in raw_message.get("fragments", [])
        ]

        msg = {
            "type": "PRIVMSG",
            "platform": "twitch",
            "message_id": event.get("message_id", ""),
            "source_message_id": event.get("message_id", ""),
            "channel": event.get("broadcaster_user_login", ""),
            "nick": event.get("chatter_user_login", ""),
            "display_name": event.get("chatter_user_name", event.get("chatter_user_login", "")),
            "message": raw_message.get("text", ""),
            "color": event.get("color", ""),
            "badges": {b.get("set_id"): b.get("id") for b in badges_list},
            "fragments": fragments,
            "tags": {},
            "is_mod": "moderator" in badge_ids,
            "is_sub": "subscriber" in badge_ids,
            "is_broadcaster": "broadcaster" in badge_ids,
            "is_vip": "vip" in badge_ids,
            "user_id": event.get("chatter_user_id", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            is_command = msg["message"].startswith("!")
            await self.db.execute(
                """INSERT INTO chat_log (channel, user_id, display_name, message, is_command, timestamp, platform, source_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                [msg["channel"], msg["user_id"], msg["display_name"],
                 msg["message"], 1 if is_command else 0, msg["timestamp"],
                 msg["platform"], msg["source_message_id"]],
            )
        except Exception as e:
            self.logger.error(f"[ChatDispatcher] Failed to log message: {e}")

        await self.bus.publish("chat.message.received", msg)

        if msg["message"].startswith("!"):
            parts = msg["message"].split(maxsplit=1)
            command_name = parts[0].lower()
            await self.bus.publish("chat.command.received", {
                **msg,
                "command": command_name,
                "args": parts[1] if len(parts) > 1 else "",
            })

from core.base_plugin import BasePlugin


class ModerationActionRouterPlugin(BasePlugin):
    """Routes normalized moderation.action.requested events to the right platform API."""

    def __init__(self, twitch, youtube, event_bus, db, logger):
        self.twitch = twitch
        self.youtube = youtube
        self.bus = event_bus
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.twitch.require_scopes([
            "moderator:manage:banned_users",
            "moderator:manage:chat_messages",
        ])
        try:
            self.youtube.require_scopes(["https://www.googleapis.com/auth/youtube.force-ssl"])
        except Exception:
            pass
        await self.bus.subscribe("moderation.action.requested", self._on_requested)

    async def _on_requested(self, event):
        data = event.payload
        platform = data.get("platform", "twitch")
        action = data.get("action")
        user = data.get("user") or {}
        user_platform_id = user.get("platform_id") or data.get("user_id") or data.get("twitch_id", "")
        display_name = user.get("display_name") or data.get("display_name", user_platform_id)
        channel_id = data.get("channel_id")
        reason = data.get("reason") or f"Manual {action}"
        duration_s = data.get("duration_s")
        rule_id = data.get("rule_id")
        message_id = data.get("message_id")

        try:
            if platform == "twitch":
                await self._apply_twitch(action, user_platform_id, message_id, duration_s, reason)
            elif platform == "youtube":
                await self._apply_youtube(action, channel_id, user_platform_id, message_id, duration_s)
            else:
                raise ValueError(f"Unsupported moderation platform: {platform}")

            await self._log(platform, channel_id, user_platform_id, display_name, action, reason, rule_id)
            await self.bus.publish("moderation.action.taken", {
                "platform": platform,
                "channel_id": channel_id,
                "message_id": message_id,
                "user": {
                    "id": user.get("id") or (f"{platform}:{user_platform_id}" if user_platform_id else ""),
                    "platform_id": user_platform_id,
                    "display_name": display_name,
                },
                "action": action,
                "duration_s": duration_s,
                "reason": reason,
                "rule_id": rule_id,
            })
        except Exception as e:
            self.logger.error(f"[ModerationRouter] {platform}.{action} failed for {display_name}: {e}")

    async def _apply_twitch(self, action: str, user_id: str, message_id: str | None, duration_s: int | None, reason: str):
        session = self.twitch.get_session()
        if not session:
            raise RuntimeError("Twitch session not active")
        broadcaster_id = session["broadcaster_id"]
        access_token = session["access_token"]
        if action == "delete":
            if not message_id:
                raise ValueError("message_id is required for delete")
            await self.twitch.delete(
                "/moderation/chat",
                params={"broadcaster_id": broadcaster_id, "moderator_id": broadcaster_id, "message_id": message_id},
                user_token=access_token,
            )
        elif action in ("ban", "timeout"):
            body = {"user_id": user_id, "reason": reason}
            if action == "timeout":
                body["duration"] = duration_s or 600
            await self.twitch.post(
                f"/moderation/bans?broadcaster_id={broadcaster_id}&moderator_id={broadcaster_id}",
                body={"data": body},
                user_token=access_token,
            )
        elif action == "unban":
            await self.twitch.delete(
                "/moderation/bans",
                params={"broadcaster_id": broadcaster_id, "moderator_id": broadcaster_id, "user_id": user_id},
                user_token=access_token,
            )
        else:
            raise ValueError(f"Unsupported Twitch moderation action: {action}")

    async def _apply_youtube(self, action: str, live_chat_id: str | None, user_channel_id: str, message_id: str | None, duration_s: int | None):
        if action == "delete":
            if not message_id:
                raise ValueError("message_id is required for delete")
            await self.youtube.delete_message(message_id)
        elif action in ("ban", "timeout"):
            if not live_chat_id or not user_channel_id:
                raise ValueError("channel_id and user.platform_id are required for YouTube ban/timeout")
            await self.youtube.ban_user(live_chat_id, user_channel_id, duration_s if action == "timeout" else None)
        elif action == "unban":
            raise ValueError("YouTube unban requires a liveChatBan id and is not supported from user id")
        else:
            raise ValueError(f"Unsupported YouTube moderation action: {action}")

    async def _log(self, platform: str, channel_id: str | None, user_id: str, display_name: str, action: str, reason: str, rule_id: int | None):
        await self.db.execute(
            """INSERT INTO mod_log (platform, channel_id, twitch_id, user_id, display_name, action, reason, rule_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
            [platform, channel_id, user_id if platform == "twitch" else "", user_id, display_name, action, reason, rule_id],
        )

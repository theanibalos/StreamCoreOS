import asyncio
from datetime import datetime, timezone
from core.base_plugin import BasePlugin


class YouTubeChatPollerPlugin(BasePlugin):
    """Polls YouTube Live Chat and publishes normalized chat events."""

    def __init__(self, youtube, event_bus, db, logger):
        self.youtube = youtube
        self.bus = event_bus
        self.db = db
        self.logger = logger
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._seen: set[str] = set()

    async def on_boot(self):
        self._task = asyncio.create_task(self._run())

    async def shutdown(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        live_chat_id = None
        page_token = None
        first_page = True
        while not self._stop.is_set():
            try:
                if not self.youtube.get_session():
                    await asyncio.sleep(5)
                    continue

                if not live_chat_id:
                    live_chat_id = await self.youtube.get_live_chat_id()
                    page_token = None
                    first_page = True
                    if not live_chat_id:
                        await asyncio.sleep(15)
                        continue
                    self.logger.info(f"[YouTubeChatPoller] Live chat detected: {live_chat_id}")

                resp = await self.youtube.list_chat_messages(live_chat_id, page_token=page_token)
                page_token = resp.get("nextPageToken") or page_token
                wait_s = max(1.0, int(resp.get("pollingIntervalMillis", 5000)) / 1000)

                items = resp.get("items", [])
                if first_page:
                    # Initial request returns recent events. Mark them as seen so we only emit new messages.
                    self._seen.update(i.get("id", "") for i in items)
                    first_page = False
                else:
                    for item in items:
                        await self._handle_item(item, live_chat_id)

                if resp.get("offlineAt"):
                    self.logger.info("[YouTubeChatPoller] Live chat is offline/ended")
                    live_chat_id = None
                    page_token = None
                    await asyncio.sleep(15)
                else:
                    await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                msg = str(e)
                self.logger.error(f"[YouTubeChatPoller] {msg}")
                if "liveChatEnded" in msg or "liveChatNotFound" in msg or "liveChatDisabled" in msg:
                    live_chat_id = None
                    page_token = None
                await asyncio.sleep(10)

    async def _handle_item(self, item: dict, live_chat_id: str):
        message_id = item.get("id", "")
        if not message_id or message_id in self._seen:
            return
        self._seen.add(message_id)
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-2000:])

        snippet = item.get("snippet", {})
        author = item.get("authorDetails", {})
        msg_type = snippet.get("type", "")

        if msg_type == "chatEndedEvent":
            return
        if msg_type == "tombstone":
            await self.bus.publish("chat.message.deleted", {
                "platform": "youtube",
                "channel_id": live_chat_id,
                "message_id": message_id,
                "raw": item,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return

        text = self._text_for(snippet, msg_type)
        if not text and msg_type not in ("newSponsorEvent", "membershipGiftingEvent", "giftMembershipReceivedEvent"):
            return

        author_channel_id = snippet.get("authorChannelId") or author.get("channelId", "")
        display_name = author.get("displayName", author_channel_id)
        user_id = f"youtube:{author_channel_id}" if author_channel_id else ""
        now = datetime.now(timezone.utc).isoformat()
        session = self.youtube.get_session() or {}

        msg = {
            "platform": "youtube",
            "channel_id": live_chat_id,
            "channel_name": session.get("channel_title") or "youtube",
            "message_id": message_id,
            "message": text,
            "color": "",
            "badges": self._badges(author),
            "fragments": [{"type": "text", "text": text}],
            "user": {
                "id": user_id,
                "platform_id": author_channel_id,
                "login": None,
                "display_name": display_name,
                "avatar_url": author.get("profileImageUrl"),
            },
            "roles": {
                "broadcaster": bool(author.get("isChatOwner")),
                "moderator": bool(author.get("isChatModerator")),
                "subscriber": bool(author.get("isChatSponsor")),
                "vip": False,
                "verified": bool(author.get("isVerified")),
            },
            "raw": item,
            "timestamp": now,
        }

        try:
            is_command = text.startswith("!")
            await self.db.execute(
                """INSERT INTO chat_log (channel, user_id, display_name, message, is_command, timestamp, platform, source_message_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                [live_chat_id, user_id, display_name, text, 1 if is_command else 0, now, "youtube", message_id],
            )
        except Exception as e:
            self.logger.error(f"[YouTubeChatPoller] Failed to log message: {e}")

        await self.bus.publish("chat.message.received", msg)
        if text.startswith("!"):
            parts = text.split(maxsplit=1)
            await self.bus.publish("chat.command.received", {
                **msg,
                "command": parts[0].lower(),
                "args": parts[1] if len(parts) > 1 else "",
            })

        await self._publish_monetization(item, msg_type, display_name, user_id, text)

    def _text_for(self, snippet: dict, msg_type: str) -> str:
        if msg_type == "textMessageEvent":
            return snippet.get("textMessageDetails", {}).get("messageText", "")
        if msg_type == "superChatEvent":
            details = snippet.get("superChatDetails", {})
            return details.get("userComment", "")
        if msg_type == "memberMilestoneChatEvent":
            return snippet.get("memberMilestoneChatDetails", {}).get("userComment", "")
        if msg_type == "superStickerEvent":
            details = snippet.get("superStickerDetails", {})
            return details.get("superStickerMetadata", {}).get("altText", "Super Sticker")
        if msg_type == "newSponsorEvent":
            return "se hizo miembro del canal"
        if msg_type == "membershipGiftingEvent":
            count = snippet.get("membershipGiftingDetails", {}).get("giftMembershipsCount", 0)
            return f"regaló {count} membresías"
        if msg_type == "giftMembershipReceivedEvent":
            return "recibió una membresía de regalo"
        return snippet.get("displayMessage", "")

    def _badges(self, author: dict) -> list[dict]:
        badges = []
        if author.get("isChatOwner"):
            badges.append({"set": "owner", "version": "1"})
        if author.get("isChatModerator"):
            badges.append({"set": "moderator", "version": "1"})
        if author.get("isChatSponsor"):
            badges.append({"set": "member", "version": "1"})
        if author.get("isVerified"):
            badges.append({"set": "verified", "version": "1"})
        return badges

    async def _publish_monetization(self, item: dict, msg_type: str, display_name: str, user_id: str, text: str):
        snippet = item.get("snippet", {})
        if msg_type == "superChatEvent":
            d = snippet.get("superChatDetails", {})
            await self.bus.publish("youtube.superchat.received", {
                "platform": "youtube",
                "id": item.get("id", ""),
                "user": display_name,
                "user_id": user_id,
                "amount_micros": d.get("amountMicros", 0),
                "currency": d.get("currency", ""),
                "display_amount": d.get("amountDisplayString", ""),
                "message": text,
            })
        elif msg_type == "superStickerEvent":
            d = snippet.get("superStickerDetails", {})
            await self.bus.publish("youtube.supersticker.received", {
                "platform": "youtube",
                "id": item.get("id", ""),
                "user": display_name,
                "user_id": user_id,
                "display_amount": d.get("amountDisplayString", ""),
                "message": text,
            })

import re
import json
import asyncio
import base64
from core.base_plugin import BasePlugin

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


class TtsRedemptionPlugin(BasePlugin):
    """
    Speaks the user_input of a channel point redemption via TTS.

    The streamer sets the reward name (e.g. "TTS") in /tts/settings →
    redemption_title. When a viewer redeems that reward and types a message,
    the plugin generates audio and pushes it to the SSE overlay.

    All TTS filters (max_length, skip_links, blocked_words) apply.
    The user's assigned voice is used; falls back to the default voice.
    """

    def __init__(self, tts, db, twitch, event_bus, logger):
        self.tts    = tts
        self.db     = db
        self.twitch = twitch
        self.bus    = event_bus
        self.logger = logger
        self._queues: dict[str, asyncio.Queue] = {}

    async def on_boot(self):
        self.twitch.register(
            "channel.channel_points_custom_reward_redemption.add",
            "1",
            ["channel:read:redemptions"],
        )
        self.twitch.on_event(
            "channel.channel_points_custom_reward_redemption.add",
            self._on_redemption,
        )
        self.logger.info("[TTS] Redemption listener ready.")

    async def _on_redemption(self, event: dict):
        settings = await self._get_settings()
        if not settings.get("enabled", True):
            return

        redemption_title: str = settings.get("redemption_title", "").strip()
        if not redemption_title:
            return  # feature not configured

        reward_title: str = event.get("reward", {}).get("title", "")
        if reward_title.lower() != redemption_title.lower():
            return  # different reward, ignore

        tts_text: str = (event.get("user_input") or "").strip()
        if not tts_text:
            return

        twitch_id:    str = event.get("user_id", "")
        twitch_login: str = event.get("user_login", event.get("user_name", twitch_id))
        display_name: str = event.get("user_name", twitch_login)

        # Apply filters
        max_len = int(settings.get("max_message_length") or 200)
        if len(tts_text) > max_len:
            tts_text = tts_text[:max_len]

        if settings.get("skip_links"):
            tts_text = _URL_RE.sub("", tts_text).strip()
            if not tts_text:
                return

        try:
            blocked: list[str] = json.loads(settings.get("blocked_words") or "[]")
        except Exception:
            blocked = []
        if blocked and any(w.lower() in tts_text.lower() for w in blocked):
            return

        voice_id = await self._get_user_voice(twitch_id)

        self.logger.info(f"[TTS] Redemption from {display_name} — '{reward_title}': {tts_text[:50]}")

        if twitch_id not in self._queues:
            self._queues[twitch_id] = asyncio.Queue()
            asyncio.create_task(self._user_worker(twitch_id))

        await self._queues[twitch_id].put((display_name, tts_text, voice_id))

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _user_worker(self, twitch_id: str):
        queue = self._queues[twitch_id]
        try:
            while True:
                username, text, voice_id = await asyncio.wait_for(queue.get(), timeout=60)
                await self._generate_and_emit(username, text, voice_id)
                queue.task_done()
        except asyncio.TimeoutError:
            pass
        finally:
            self._queues.pop(twitch_id, None)

    async def _generate_and_emit(self, username: str, text: str, voice_id: str):
        try:
            audio_bytes = await self.tts.generate(text, voice_id)
            audio_b64   = base64.b64encode(audio_bytes).decode("utf-8")
            await self.bus.publish("tts.audio.ready", {
                "username":  username,
                "text":      text,
                "voice_id":  voice_id,
                "audio_b64": audio_b64,
            })
        except Exception as e:
            code = getattr(e, "code", None)
            if code:
                self.logger.error(f"[TTS] Redemption TTSError({code}) for '{username}': {e}")
            else:
                self.logger.error(f"[TTS] Redemption unexpected error for '{username}': {e}")

    async def _get_settings(self) -> dict:
        return await self.db.query_one("SELECT * FROM tts_settings WHERE id = 1") or {}

    async def _get_user_voice(self, twitch_id: str) -> str | None:
        row = await self.db.query_one(
            "SELECT voice_id FROM tts_user_voice WHERE twitch_id = $1", [twitch_id]
        )
        return row["voice_id"] if row else None

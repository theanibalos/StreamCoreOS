import re
import json
import asyncio
import base64
from core.base_plugin import BasePlugin

# Regex to detect URLs
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


class TtsListenerPlugin(BasePlugin):
    """
    Listens for chat.message.received events.
    Messages starting with !tts are converted to audio via the tts tool
    and pushed into a queue consumed by TtsStreamPlugin via event_bus.
    """

    def __init__(self, tts, db, event_bus, logger):
        self.tts       = tts
        self.db        = db
        self.bus       = event_bus
        self.logger    = logger
        self._cooldowns: dict[str, float] = {}
        self._queues: dict[str, asyncio.Queue] = {}   # per-user generation queue

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self.on_message)
        self.logger.info("[TTS] Listener ready — waiting for !tts commands.")

    async def on_message(self, data: dict):
        text_raw: str = data.get("message", "")
        if not text_raw.lower().startswith("!tts "):
            return

        settings = await self._get_settings()
        if not settings.get("enabled", True):
            return

        twitch_id:    str  = data.get("user_id", "")
        twitch_login: str  = (
            data.get("nick")
            or data.get("username")
            or data.get("user_login")
            or twitch_id
        )
        is_sub:         bool = data.get("is_sub", False)
        is_mod:         bool = data.get("is_mod", False)
        is_broadcaster: bool = data.get("is_broadcaster", False)
        is_privileged:  bool = is_mod or is_broadcaster

        # Mods and broadcaster always bypass all access restrictions
        if not is_privileged:
            # Sub-only filter
            if settings.get("sub_only") and not is_sub:
                return

        # Cooldown filter
        cooldown = int(settings.get("cooldown_seconds") or 0)
        if cooldown > 0 and not await self._check_cooldown(twitch_id, cooldown):
            return

        # Extract TTS text (strip "!tts ")
        tts_text = text_raw[5:].strip()
        if not tts_text:
            return

        # Max length filter
        max_len = int(settings.get("max_message_length") or 200)
        if len(tts_text) > max_len:
            tts_text = tts_text[:max_len]

        # Skip commands filter (!xxx at start)
        if settings.get("skip_commands") and tts_text.startswith("!"):
            return

        # Skip links filter
        if settings.get("skip_links") and _URL_RE.search(tts_text):
            tts_text = _URL_RE.sub("", tts_text).strip()
            if not tts_text:
                return

        # Blocked words filter
        try:
            blocked: list[str] = json.loads(settings.get("blocked_words") or "[]")
        except Exception:
            blocked = []
        if blocked:
            lower = tts_text.lower()
            if any(w.lower() in lower for w in blocked):
                return

        # Resolve voice for this user
        voice_id = await self._get_user_voice(twitch_id)

        if twitch_id not in self._queues:
            self._queues[twitch_id] = asyncio.Queue()
            asyncio.create_task(self._user_worker(twitch_id))

        await self._queues[twitch_id].put((twitch_login, tts_text, voice_id))

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _user_worker(self, twitch_id: str):
        queue = self._queues[twitch_id]
        try:
            while True:
                username, text, voice_id = await asyncio.wait_for(queue.get(), timeout=60)
                await self._generate_and_emit(twitch_id, username, text, voice_id)
                queue.task_done()
        except asyncio.TimeoutError:
            pass
        finally:
            self._queues.pop(twitch_id, None)

    async def _generate_and_emit(self, twitch_id: str, username: str, text: str, voice_id: str):
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
                self.logger.error(f"[TTS] TTSError({code}) for '{username}': {e}")
            else:
                self.logger.error(f"[TTS] Unexpected error generating audio for '{username}': {e}")

    async def _get_settings(self) -> dict:
        row = await self.db.query_one("SELECT * FROM tts_settings WHERE id = 1")
        return row or {}

    async def _get_user_voice(self, twitch_id: str) -> str | None:
        row = await self.db.query_one(
            "SELECT voice_id FROM tts_user_voice WHERE twitch_id = $1", [twitch_id]
        )
        return row["voice_id"] if row else None

    async def _check_cooldown(self, twitch_id: str, cooldown_s: int) -> bool:
        """Returns True if user is allowed to speak (not in cooldown)."""
        import time
        now   = time.monotonic()
        last  = self._cooldowns.get(twitch_id, 0.0)
        if now - last < cooldown_s:
            return False
        self._cooldowns[twitch_id] = now
        return True

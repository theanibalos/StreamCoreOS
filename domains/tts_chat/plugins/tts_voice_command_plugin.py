from core.base_plugin import BasePlugin


# Max voices shown inline in chat response
_INLINE_VOICE_LIMIT = 5


def _short_name(full_name: str) -> str:
    """
    Extract the first meaningful word from a voice full name.
    'Microsoft Alvaro Online (Natural)' → 'Alvaro'
    'Google es-ES-Standard-A' → 'es-ES-Standard-A'
    """
    # Strip known prefixes
    for prefix in ("Microsoft ", "Google ", "Amazon "):
        if full_name.startswith(prefix):
            full_name = full_name[len(prefix):]
    # Take first word
    return full_name.split()[0] if full_name else full_name


class TtsVoiceCommandPlugin(BasePlugin):
    """
    Handles !voz command in Twitch chat.

    Usage:
        !voz                    → replies with the user's current voice
        !voz list               → replies with popular Spanish/English voices
        !voz list es            → replies with voices matching locale prefix
        !voz <voice_id>         → assigns that voice to the user
        !voz reset              → removes voice assignment (returns to default)
    """

    def __init__(self, tts, db, event_bus, twitch, logger):
        self.tts    = tts
        self.db     = db
        self.bus    = event_bus
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.message.received", self.on_message)

    async def on_message(self, data: dict):
        text: str = data.get("message", "").strip()
        if not text.lower().startswith("!voz"):
            return

        channel:      str = data.get("channel", "")
        twitch_id:    str = data.get("user_id", "")
        twitch_login: str = (
            data.get("nick")
            or data.get("username")
            or data.get("user_login")
            or twitch_id   # fallback: use ID if login not available
        )
        display_name: str = data.get("display_name", twitch_login)

        parts = text.split(None, 2)   # ["!voz", arg1?, arg2?]
        arg1  = parts[1].lower() if len(parts) > 1 else ""
        arg2  = parts[2]         if len(parts) > 2 else ""

        if not arg1:
            await self._reply_current_voice(channel, twitch_id, display_name)

        elif arg1 == "list":
            locale_prefix = arg2.strip().lower() if arg2 else "es"
            await self._reply_voice_list(channel, locale_prefix)

        elif arg1 == "reset":
            await self._reset_voice(channel, twitch_id, twitch_login, display_name)

        else:
            # treat arg1 as voice_id
            voice_id = parts[1]   # preserve original case
            await self._assign_voice(channel, twitch_id, twitch_login, display_name, voice_id)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _reply_current_voice(self, channel: str, twitch_id: str, display_name: str):
        row = await self.db.query_one(
            "SELECT voice_id, voice_name FROM tts_user_voice WHERE twitch_id = $1",
            [twitch_id],
        )
        if row:
            msg = f"@{display_name} Tu voz actual es: {row['voice_name']} ({row['voice_id']})"
        else:
            default = self.tts.get_default_voice()
            msg = f"@{display_name} No tienes voz asignada. Se usa la voz por defecto: {default}  •  Usa !voz <id> para cambiarla o !voz list para ver opciones."
        await self._send(channel, msg)

    async def _reply_voice_list(self, channel: str, locale_prefix: str):
        try:
            voices = await self.tts.list_voices()
            filtered = [v for v in voices if v["locale"].lower().startswith(locale_prefix)]
            if not filtered:
                await self._send(channel, f"No encontré voces para '{locale_prefix}'. Prueba con 'es', 'en', 'fr', etc.")
                return

            sample = filtered[:_INLINE_VOICE_LIMIT]
            # Show short name (first part before space) so it's easy to type
            lines  = "  •  ".join(f"{_short_name(v['name'])} ({v['gender'][0]})" for v in sample)
            total  = len(filtered)
            more   = f" — y {total - _INLINE_VOICE_LIMIT} más" if total > _INLINE_VOICE_LIMIT else ""
            await self._send(channel, f"Voces [{locale_prefix}]{more}: {lines}  —  Úsala con !voz <nombre>")
        except Exception as e:
            self.logger.error(f"[TTS] _reply_voice_list error: {e}")
            await self._send(channel, "Error al obtener la lista de voces.")

    async def _assign_voice(
        self,
        channel: str,
        twitch_id: str,
        twitch_login: str,
        display_name: str,
        voice_id: str,
    ):
        try:
            voices = await self.tts.list_voices()
            query  = voice_id.lower()
            # 1. Exact ID match  (es-ES-AlvaroNeural)
            # 2. Short name match (alvaro → Microsoft Alvaro Online...)
            match = (
                next((v for v in voices if v["id"].lower() == query), None)
                or next((v for v in voices if query in _short_name(v["name"]).lower()), None)
                or next((v for v in voices if query in v["name"].lower()), None)
            )
            if not match:
                await self._send(
                    channel,
                    f"@{display_name} No encontré la voz '{voice_id}'. Usa !voz list para ver opciones.",
                )
                return

            await self.db.execute(
                """
                INSERT INTO tts_user_voice (twitch_id, twitch_login, voice_id, voice_name, updated_at)
                VALUES ($1, $2, $3, $4, datetime('now'))
                ON CONFLICT (twitch_id) DO UPDATE SET
                    twitch_login = excluded.twitch_login,
                    voice_id     = excluded.voice_id,
                    voice_name   = excluded.voice_name,
                    updated_at   = datetime('now')
                """,
                [twitch_id, twitch_login, match["id"], match["name"]],
            )
            self.logger.info(f"[TTS] {twitch_login} set voice → {match['id']}")
            await self._send(channel, f"@{display_name} ¡Voz cambiada a {match['name']}! Pruébala con !tts hola.")

        except Exception as e:
            self.logger.error(f"[TTS] _assign_voice error: {e}")
            await self._send(channel, f"@{display_name} Error al asignar la voz.")

    async def _reset_voice(
        self, channel: str, twitch_id: str, twitch_login: str, display_name: str
    ):
        count = await self.db.execute(
            "DELETE FROM tts_user_voice WHERE twitch_id = $1", [twitch_id]
        )
        if count:
            default = self.tts.get_default_voice()
            self.logger.info(f"[TTS] {twitch_login} reset voice → default ({default})")
            await self._send(channel, f"@{display_name} Voz reseteada. Ahora usas la voz por defecto: {default}")
        else:
            await self._send(channel, f"@{display_name} No tenías voz asignada.")

    async def _send(self, channel: str, message: str):
        try:
            await self.twitch.send_message(channel, message)
        except Exception as e:
            self.logger.error(f"[TTS] Failed to send chat message: {e}")

import json
from core.base_plugin import BasePlugin


class TtsRestoreConfigPlugin(BasePlugin):
    """Reads behavioral TTS settings from DB on boot and pushes them into the tts tool."""

    def __init__(self, db, tts, logger):
        self.db     = db
        self.tts    = tts
        self.logger = logger

    async def on_boot(self):
        row = await self.db.query_one("SELECT * FROM tts_settings WHERE id = 1")
        if not row:
            self.logger.warning("[TTS] No settings row found — using defaults.")
            return

        try:
            blocked = json.loads(row.get("blocked_words") or "[]")
        except Exception:
            blocked = []

        self.tts.load_config({
            "enabled":            bool(row["enabled"]),
            "default_voice":      row["default_voice"],
            "max_message_length": row["max_message_length"],
            "skip_commands":      bool(row["skip_commands"]),
            "skip_links":         bool(row["skip_links"]),
            "sub_only":           bool(row["sub_only"]),
            "mod_bypass":         bool(row.get("mod_bypass", True)),
            "cooldown_seconds":   row["cooldown_seconds"],
            "blocked_words":      blocked,
            "redemption_title":   row.get("redemption_title", ""),
        })
        self.logger.info(
            f"[TTS] Config restored — default voice: {row['default_voice']}, "
            f"enabled: {bool(row['enabled'])}, providers: {self.tts.get_providers()}"
        )

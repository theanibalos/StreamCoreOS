import json
from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class TtsSettingsData(BaseModel):
    enabled:            bool
    default_voice:      str
    max_message_length: int
    skip_commands:      bool
    skip_links:         bool
    sub_only:           bool
    mod_bypass:         bool
    cooldown_seconds:   int
    blocked_words:      list[str]
    redemption_title:   str
    providers:          dict[str, bool]
    updated_at:         str


class TtsSettingsResponse(BaseModel):
    success: bool
    data:    Optional[TtsSettingsData] = None
    error:   Optional[str] = None


class UpdateTtsSettingsRequest(BaseModel):
    enabled:            Optional[bool]      = None
    default_voice:      Optional[str]       = Field(default=None, max_length=200)
    max_message_length: Optional[int]       = Field(default=None, ge=10, le=500)
    skip_commands:      Optional[bool]      = None
    skip_links:         Optional[bool]      = None
    sub_only:           Optional[bool]      = None
    mod_bypass:         Optional[bool]      = None
    cooldown_seconds:   Optional[int]       = Field(default=None, ge=0, le=3600)
    blocked_words:      Optional[list[str]] = None
    redemption_title:   Optional[str]       = Field(default=None, max_length=100)


class TtsSettingsPlugin(BasePlugin):
    """
    GET /tts/settings  — returns current TTS configuration.
    PUT /tts/settings  — updates behavioral settings (provider config is in .env).
    """

    def __init__(self, db, tts, http, logger):
        self.db     = db
        self.tts    = tts
        self.http   = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/tts/settings", "GET", self.get_settings,
            tags=["TTS"],
            response_model=TtsSettingsResponse,
        )
        self.http.add_endpoint(
            "/tts/settings", "PUT", self.update_settings,
            tags=["TTS"],
            request_model=UpdateTtsSettingsRequest,
            response_model=TtsSettingsResponse,
        )

    async def get_settings(self, data: dict, context=None):
        row = await self.db.query_one("SELECT * FROM tts_settings WHERE id = 1")
        if not row:
            return {"success": False, "error": "Settings not found"}
        return {"success": True, "data": self._row_to_data(row)}

    async def update_settings(self, data: dict, context=None):
        try:
            req     = UpdateTtsSettingsRequest(**data)
            updates = req.model_dump(exclude_none=True)
            if not updates:
                return {"success": False, "error": "No fields to update"}

            if "blocked_words" in updates:
                updates["blocked_words"] = json.dumps(updates["blocked_words"])

            sets   = ", ".join(f"{k} = ${i+1}" for i, k in enumerate(updates))
            values = list(updates.values())

            await self.db.execute(
                f"UPDATE tts_settings SET {sets}, updated_at = datetime('now') WHERE id = 1",
                values,
            )

            row = await self.db.query_one("SELECT * FROM tts_settings WHERE id = 1")
            self.tts.load_config(self._row_to_config(row))

            self.logger.info(f"[TTS] Settings updated: {list(updates.keys())}")
            return {"success": True, "data": self._row_to_data(row)}
        except Exception as e:
            self.logger.error(f"[TTS] Failed to update settings: {e}")
            return {"success": False, "error": str(e)}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _row_to_config(self, row: dict) -> dict:
        try:
            blocked = json.loads(row.get("blocked_words") or "[]")
        except Exception:
            blocked = []
        return {
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
        }

    def _row_to_data(self, row: dict) -> dict:
        try:
            blocked = json.loads(row.get("blocked_words") or "[]")
        except Exception:
            blocked = []
        return {
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
            "providers":          self.tts.get_providers(),
            "updated_at":         row["updated_at"],
        }

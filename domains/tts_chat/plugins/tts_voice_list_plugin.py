from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin
from tools.tts.tts_tool import TTSError


class VoiceItem(BaseModel):
    id:       str
    name:     str
    gender:   str
    locale:   str
    provider: str


class VoiceListResponse(BaseModel):
    success: bool
    data:    Optional[list[VoiceItem]] = None
    error:   Optional[str] = None


class TtsVoiceListPlugin(BasePlugin):
    """
    GET /tts/voices           — all voices for the active provider.
    GET /tts/voices?locale=es — filter by locale prefix (e.g. "es", "en-US").
    GET /tts/voices?gender=Female — filter by gender.
    """

    def __init__(self, tts, http, logger):
        self.tts    = tts
        self.http   = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/tts/voices", "GET", self.list_voices,
            tags=["TTS"],
            response_model=VoiceListResponse,
        )

    async def list_voices(self, data: dict, context=None):
        try:
            voices = await self.tts.list_voices()

            locale_filter: str = data.get("locale", "").strip().lower()
            gender_filter: str = data.get("gender", "").strip().lower()

            if locale_filter:
                voices = [v for v in voices if v["locale"].lower().startswith(locale_filter)]
            if gender_filter:
                voices = [v for v in voices if v["gender"].lower() == gender_filter]

            return {"success": True, "data": voices}
        except TTSError as e:
            self.logger.error(f"[TTS] list_voices error ({e.code}): {e}")
            return {"success": False, "error": str(e)}
        except Exception as e:
            self.logger.error(f"[TTS] list_voices unexpected error: {e}")
            return {"success": False, "error": str(e)}

import io
import os
from tools.tts.errors import TTSError
from tools.tts.providers.base import TTSProvider


_DEFAULT_VOICE = os.getenv("EDGE_TTS_DEFAULT_VOICE", "es-ES-AlvaroNeural")


class EdgeTTSProvider(TTSProvider):
    """
    Microsoft Edge TTS via the edge-tts library.
    Free, ~322 voices, no external service needed — always available.

    Env vars (all optional):
        EDGE_TTS_DEFAULT_VOICE   default voice ID (default: es-ES-AlvaroNeural)
    """

    @property
    def name(self) -> str:
        return "edge_tts"

    async def setup(self) -> None:
        print(f"[EdgeTTS] Ready — default voice: {_DEFAULT_VOICE}")

    def is_available(self) -> bool:
        return True

    def get_default_voice(self) -> str:
        return _DEFAULT_VOICE

    async def generate(self, text: str, voice_id: str) -> bytes:
        try:
            import edge_tts
            buffer = io.BytesIO()
            communicate = edge_tts.Communicate(text, voice_id)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.write(chunk["data"])
            audio = buffer.getvalue()
            if not audio:
                raise TTSError("generation_failed", f"edge_tts returned empty audio for voice '{voice_id}'")
            return audio
        except TTSError:
            raise
        except Exception as e:
            msg = str(e).lower()
            if "voice" in msg or "not found" in msg or "invalid" in msg:
                raise TTSError("voice_not_found", f"Voice '{voice_id}' not found in edge_tts: {e}")
            raise TTSError("generation_failed", f"edge_tts generation failed: {e}")

    async def list_voices(self) -> list[dict]:
        try:
            import edge_tts
            raw = await edge_tts.list_voices()
            return [
                {
                    "id":       v["ShortName"],
                    "name":     v["FriendlyName"],
                    "gender":   v["Gender"],
                    "locale":   v["Locale"],
                    "provider": self.name,
                }
                for v in raw
            ]
        except Exception as e:
            raise TTSError("generation_failed", f"Could not list edge_tts voices: {e}")

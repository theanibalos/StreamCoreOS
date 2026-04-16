import asyncio
import os
import httpx
from tools.tts.errors import TTSError
from tools.tts.providers.base import TTSProvider


_HOST    = os.getenv("VOICEBOX_HOST", "localhost")
_PORT    = int(os.getenv("VOICEBOX_PORT", "17493"))
_TIMEOUT = int(os.getenv("VOICEBOX_TIMEOUT_S", "60"))
_POLL_INTERVAL = 1.0   # seconds between status polls


class VoiceboxProvider(TTSProvider):
    """
    Voicebox REST API (http://host:port).

    Generation uses the async flow to avoid holding an HTTP connection open
    for the full synthesis duration (can be 30-60s on CPU):
        1. POST /generate       → job id
        2. Poll GET /history/id → wait for status == "completed"
        3. GET  /audio/id       → raw WAV bytes

    Env vars:
        VOICEBOX_HOST         host (default: localhost)
        VOICEBOX_PORT         port (default: 17493)
        VOICEBOX_TIMEOUT_S    max seconds to wait for synthesis (default: 60)
    """

    @property
    def name(self) -> str:
        return "voicebox"

    async def setup(self) -> None:
        self._available = False
        self._client = httpx.AsyncClient(
            base_url=f"http://{_HOST}:{_PORT}",
            timeout=10,
        )
        await self._ping()

    def is_available(self) -> bool:
        return self._available

    def get_default_voice(self) -> str:
        return ""

    async def generate(self, text: str, voice_id: str) -> bytes:
        if not self._available:
            raise TTSError("provider_unavailable", "Voicebox is not reachable.")
        try:
            gen_id = await self._submit(text, voice_id)
            await self._wait_until_done(gen_id)
            return await self._fetch_audio(gen_id)
        except TTSError:
            raise
        except httpx.TimeoutException:
            raise TTSError("timeout", "Voicebox request timed out.")
        except httpx.ConnectError:
            self._available = False
            raise TTSError("connection_error", "Cannot connect to Voicebox.")
        except Exception as e:
            raise TTSError("generation_failed", f"Voicebox generation failed: {e}")

    async def list_voices(self) -> list[dict]:
        if not self._available:
            raise TTSError("provider_unavailable", "Voicebox is not reachable.")
        try:
            resp = await self._client.get("/profiles")
            resp.raise_for_status()
            profiles = resp.json()
            items = profiles if isinstance(profiles, list) else profiles.get("profiles", [])
            return [
                {
                    "id":       p.get("id", ""),
                    "name":     p.get("name", ""),
                    "gender":   p.get("gender", ""),
                    "locale":   p.get("language", ""),
                    "provider": self.name,
                }
                for p in items
            ]
        except TTSError:
            raise
        except Exception as e:
            raise TTSError("generation_failed", f"Could not list Voicebox voices: {e}")

    async def shutdown(self) -> None:
        await self._client.aclose()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _ping(self) -> None:
        try:
            resp = await self._client.get("/health", timeout=5)
            self._available = resp.status_code < 500
            status = "OK" if self._available else f"DEGRADED ({resp.status_code})"
            print(f"[Voicebox] Ping → {status} ({_HOST}:{_PORT})")
        except Exception as e:
            self._available = False
            print(f"[Voicebox] Unreachable at {_HOST}:{_PORT}: {e}")

    async def _submit(self, text: str, voice_id: str) -> str:
        resp = await self._client.post(
            "/generate",
            json={"profile_id": voice_id, "text": text},
            timeout=10,
        )
        if resp.status_code == 404:
            raise TTSError("voice_not_found", f"Voice profile '{voice_id}' not found in Voicebox.")
        if resp.status_code >= 400:
            raise TTSError("generation_failed", f"Voicebox /generate returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()["id"]

    async def _wait_until_done(self, gen_id: str) -> None:
        deadline = asyncio.get_event_loop().time() + _TIMEOUT
        while True:
            if asyncio.get_event_loop().time() > deadline:
                raise TTSError("timeout", f"Voicebox synthesis timed out after {_TIMEOUT}s.")
            await asyncio.sleep(_POLL_INTERVAL)
            resp = await self._client.get(f"/history/{gen_id}", timeout=10)
            if resp.status_code >= 400:
                raise TTSError("generation_failed", f"Voicebox /history returned {resp.status_code}")
            data = resp.json()
            status = data.get("status", "")
            if status == "completed":
                return
            if status in ("failed", "error"):
                raise TTSError("generation_failed", f"Voicebox generation failed: {data.get('error', status)}")

    async def _fetch_audio(self, gen_id: str) -> bytes:
        resp = await self._client.get(f"/audio/{gen_id}", timeout=30)
        if resp.status_code >= 400:
            raise TTSError("generation_failed", f"Voicebox /audio returned {resp.status_code}")
        return resp.content

import asyncio
import os
import httpx
from tools.tts.errors import TTSError
from tools.tts.providers.base import TTSProvider


_HOST          = os.getenv("VOICEBOX_HOST", "localhost")
_PORT          = int(os.getenv("VOICEBOX_PORT", "17493"))
_JOB_TIMEOUT_S = int(os.getenv("VOICEBOX_JOB_TIMEOUT_S", "300"))  # 5 min max per job
_POLL_INTERVAL = 2.0  # seconds between background poll cycles


class VoiceboxProvider(TTSProvider):
    """
    Voicebox REST API (http://host:port).

    Generation is fire-and-forget:
        1. POST /generate        → job id (returns immediately)
        2. Background loop polls GET /history/id every 2s for all pending jobs
        3. When "completed" → GET /audio/id → resolves the caller's Future

    generate() returns bytes like any other provider, but internally it awaits
    a Future resolved by the background loop. This means:
    - No hard timeout (default 5 min max via VOICEBOX_JOB_TIMEOUT_S)
    - Multiple users' jobs are polled in parallel efficiently
    - The caller's asyncio task is free to be cancelled if needed

    Env vars:
        VOICEBOX_HOST             host (default: localhost)
        VOICEBOX_PORT             port (default: 17493)
        VOICEBOX_JOB_TIMEOUT_S    max seconds per job before giving up (default: 300)
    """

    @property
    def name(self) -> str:
        return "voicebox"

    async def setup(self) -> None:
        self._available  = False
        self._client     = httpx.AsyncClient(base_url=f"http://{_HOST}:{_PORT}", timeout=10)
        self._pending: dict[str, tuple[asyncio.Future, float]] = {}  # job_id → (future, deadline)
        await self._ping()
        self._poll_task = asyncio.create_task(self._poll_loop())

    def is_available(self) -> bool:
        return self._available

    def get_default_voice(self) -> str:
        return ""

    async def generate(self, text: str, voice_id: str) -> bytes:
        if not self._available:
            raise TTSError("provider_unavailable", "Voicebox is not reachable.")
        try:
            gen_id = await self._submit(text, voice_id)
        except TTSError:
            raise
        except httpx.ConnectError:
            self._available = False
            raise TTSError("connection_error", "Cannot connect to Voicebox.")
        except Exception as e:
            raise TTSError("generation_failed", f"Voicebox submit failed: {e}")

        loop    = asyncio.get_running_loop()
        future  = loop.create_future()
        deadline = loop.time() + _JOB_TIMEOUT_S
        self._pending[gen_id] = (future, deadline)
        return await future

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
        if hasattr(self, "_poll_task"):
            self._poll_task.cancel()
        for future, _ in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        await self._client.aclose()

    # ── Background poll loop ───────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            if not self._pending:
                continue

            now = asyncio.get_running_loop().time()

            for gen_id in list(self._pending):
                future, deadline = self._pending[gen_id]

                if future.done():
                    self._pending.pop(gen_id, None)
                    continue

                if now > deadline:
                    self._pending.pop(gen_id, None)
                    future.set_exception(
                        TTSError("timeout", f"Voicebox job {gen_id[:8]} timed out after {_JOB_TIMEOUT_S}s.")
                    )
                    continue

                try:
                    resp = await self._client.get(f"/history/{gen_id}", timeout=10)
                    if resp.status_code >= 400:
                        continue  # retry next cycle
                    status = resp.json().get("status", "")

                    if status == "completed":
                        try:
                            audio = await self._fetch_audio(gen_id)
                            self._pending.pop(gen_id, None)
                            if not future.done():
                                future.set_result(audio)
                        except TTSError as e:
                            self._pending.pop(gen_id, None)
                            if not future.done():
                                future.set_exception(e)

                    elif status in ("failed", "error"):
                        self._pending.pop(gen_id, None)
                        if not future.done():
                            future.set_exception(
                                TTSError("generation_failed", f"Voicebox job failed: {status}")
                            )

                except Exception as e:
                    print(f"[Voicebox] Poll error for job {gen_id[:8]}: {e}")

    # ── HTTP helpers ──────────────────────────────────────────────────────────

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

    async def _fetch_audio(self, gen_id: str) -> bytes:
        # Voicebox marks jobs "completed" before the audio file is fully written.
        # Retry with increasing delays (2s, 4s, 6s, 8s, 10s = 30s max extra wait).
        for attempt in range(6):
            resp = await self._client.get(f"/audio/{gen_id}", timeout=30)
            if resp.status_code == 200:
                return resp.content
            if resp.status_code != 500 or attempt == 5:
                raise TTSError("generation_failed", f"Voicebox /audio returned {resp.status_code}")
            await asyncio.sleep(2 * (attempt + 1))
        raise TTSError("generation_failed", "Voicebox /audio failed after retries")

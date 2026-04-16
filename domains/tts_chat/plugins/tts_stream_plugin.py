import asyncio
from core.base_plugin import BasePlugin


class TtsStreamPlugin(BasePlugin):
    """
    SSE endpoint: GET /tts/overlay/stream

    Browsers (and OBS browser sources) connect here.
    Each connected client receives audio events in real time.

    Event format:
        data: {"username": "...", "text": "...", "voice_id": "...", "audio_b64": "..."}

    The frontend decodes audio_b64, plays it with AudioContext, and queues
    messages so they never overlap.
    """

    def __init__(self, http, event_bus, logger):
        self.http    = http
        self.bus     = event_bus
        self.logger  = logger
        self._queues: list[asyncio.Queue] = []

    async def on_boot(self):
        await self.bus.subscribe("tts.audio.ready", self._on_audio_ready)
        self.http.add_sse_endpoint(
            "/tts/overlay/stream",
            self._stream,
            tags=["TTS"],
        )
        self.logger.info("[TTS] SSE stream ready at /tts/overlay/stream")

    async def _on_audio_ready(self, data: dict):
        """Fan-out: push the audio event to every connected SSE client."""
        dead = []
        for q in self._queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._queues.remove(q)

    async def _stream(self, data: dict):
        """SSE generator — one per connected client."""
        import json
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._queues.append(queue)
        self.logger.info(f"[TTS] New SSE client connected ({len(self._queues)} total)")
        try:
            # Send a heartbeat immediately so the browser knows the connection is alive
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    payload = json.dumps({
                        "type":      "audio",
                        "username":  event["username"],
                        "text":      event["text"],
                        "voice_id":  event["voice_id"],
                        "audio_b64": event["audio_b64"],
                    })
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive ping every 25 s
                    yield "data: {\"type\": \"ping\"}\n\n"
        finally:
            if queue in self._queues:
                self._queues.remove(queue)
            self.logger.info(f"[TTS] SSE client disconnected ({len(self._queues)} remaining)")

import os
from core.base_tool import BaseTool
from tools.tts.errors import TTSError
from tools.tts.providers.base import TTSProvider
from tools.tts.providers.edge_tts import EdgeTTSProvider


class TTSTool(BaseTool):
    """
    Universal TTS router. Plugins call generate(text, voice_id) and never
    interact with providers directly.

    Voice IDs are namespaced: "<provider>:<raw_id>"
        edge_tts:es-ES-AlvaroNeural
        voicebox:b7e63948-323c-4711-be5a-1a44ef1f2be6

    All configured providers run simultaneously. If the requested provider is
    unavailable, generation falls back to the edge_tts default voice (option A).

    Provider config lives entirely in env vars. Behavioral settings
    (enabled, default_voice, filters) are pushed via load_config() from the DB.
    """

    @property
    def name(self) -> str:
        return "tts"

    async def setup(self) -> None:
        self._providers: dict[str, TTSProvider] = {}
        self._config: dict = {}

        # edge_tts is always registered
        edge = EdgeTTSProvider()
        await edge.setup()
        self._providers["edge_tts"] = edge

        # Voicebox: only if host is configured
        if os.getenv("VOICEBOX_HOST"):
            from tools.tts.providers.voicebox import VoiceboxProvider
            vb = VoiceboxProvider()
            await vb.setup()
            self._providers["voicebox"] = vb

        names = list(self._providers)
        print(f"[TTSTool] Ready — providers: {names}")

    # ── Public API (used by plugins) ──────────────────────────────────────────

    async def generate(self, text: str, voice_id: str | None = None) -> bytes:
        """
        Generate speech audio. Returns raw bytes (MP3 or WAV depending on provider).
        Falls back to edge_tts default voice if the requested provider is unavailable.
        Raises TTSError only if edge_tts itself fails (extremely rare).
        """
        voice = voice_id or self.get_default_voice()
        provider_name, raw_id = self._parse_voice(voice)
        provider = self._providers.get(provider_name)

        if provider and provider.is_available():
            try:
                return await provider.generate(text, raw_id)
            except TTSError:
                pass  # fall through to fallback

        # Fallback: edge_tts with its own default voice
        edge = self._providers["edge_tts"]
        fallback_voice = edge.get_default_voice()
        return await edge.generate(text, fallback_voice)

    async def list_voices(self) -> list[dict]:
        """
        Aggregate voices from all available providers.
        Each voice id is namespaced: "<provider>:<raw_id>".
        """
        result = []
        for provider in self._providers.values():
            if not provider.is_available():
                continue
            voices = await provider.list_voices()
            for v in voices:
                v["id"] = f"{provider.name}:{v['id']}"
            result.extend(voices)
        return result

    def is_available(self) -> bool:
        return any(p.is_available() for p in self._providers.values())

    def get_default_voice(self) -> str:
        return self._config.get("default_voice") or f"edge_tts:{self._providers['edge_tts'].get_default_voice()}"

    def get_provider(self) -> str:
        """Returns the provider name implied by the current default voice."""
        return self._parse_voice(self.get_default_voice())[0]

    def get_providers(self) -> dict[str, bool]:
        """Returns {provider_name: is_available} for all registered providers."""
        return {name: p.is_available() for name, p in self._providers.items()}

    # ── Config (behavioral settings only) ────────────────────────────────────

    def load_config(self, config: dict) -> None:
        """
        Push behavioral settings from DB. Connection config is read from env
        at setup() and never changes at runtime.
        """
        self._config = config

    def get_config(self) -> dict:
        cfg = self._config
        return {
            "enabled":            cfg.get("enabled", True),
            "default_voice":      self.get_default_voice(),
            "max_message_length": cfg.get("max_message_length", 200),
            "skip_commands":      cfg.get("skip_commands", True),
            "skip_links":         cfg.get("skip_links", True),
            "sub_only":           cfg.get("sub_only", False),
            "mod_bypass":         cfg.get("mod_bypass", True),
            "cooldown_seconds":   cfg.get("cooldown_seconds", 0),
            "blocked_words":      cfg.get("blocked_words", []),
            "redemption_title":   cfg.get("redemption_title", ""),
            "providers":          self.get_providers(),
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        for provider in self._providers.values():
            await provider.shutdown()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_voice(voice_id: str) -> tuple[str, str]:
        """Split 'provider:raw_id' → ('provider', 'raw_id'). Legacy unnamespaced IDs default to edge_tts."""
        if ":" in voice_id:
            provider, raw = voice_id.split(":", 1)
            return provider, raw
        return "edge_tts", voice_id

    # ── AI context ────────────────────────────────────────────────────────────

    def get_interface_description(self) -> str:
        return """
TTS Tool (tts):
    - PURPOSE: Universal TTS router with swappable providers. Plugins never
      interact with providers directly — just call generate(text, voice_id).
    - VOICE ID FORMAT: "<provider>:<raw_id>"
        edge_tts:es-ES-AlvaroNeural
        voicebox:b7e63948-323c-4711-be5a-1a44ef1f2be6
    - FALLBACK: If the requested provider is unavailable, falls back silently
      to the edge_tts default voice. edge_tts is always available.
    - PROVIDER CONFIG: env vars only (VOICEBOX_HOST, VOICEBOX_PORT, VOICEBOX_TIMEOUT_S,
      EDGE_TTS_DEFAULT_VOICE). Never stored in DB.
    - BEHAVIORAL CONFIG: pushed via load_config() from DB on boot and on PUT /tts/settings.
    - API:
        await generate(text, voice_id?) → bytes (MP3 or WAV)
        await list_voices()             → list[{id, name, gender, locale, provider}]
        load_config(config: dict)       → sets behavioral settings
        get_config()                    → dict (includes providers availability)
        is_available()                  → bool
        get_default_voice()             → namespaced voice id
        get_provider()                  → provider name of default voice
        get_providers()                 → dict[provider_name, is_available]
"""

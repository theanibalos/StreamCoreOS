from abc import ABC, abstractmethod


class TTSProvider(ABC):
    """
    Base class for all TTS providers.

    Each provider is responsible for its own configuration (read from env at
    instantiation) and connection lifecycle. TTSTool orchestrates them.

    Voice IDs passed to generate() and returned by list_voices() are always
    the raw provider-local ID (no namespace prefix). The namespace is added/
    stripped by TTSTool.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider name used as namespace prefix (e.g. 'edge_tts')."""
        ...

    @abstractmethod
    async def setup(self) -> None:
        """Initialise connections, ping remote services."""
        ...

    @abstractmethod
    async def generate(self, text: str, voice_id: str) -> bytes:
        """Generate audio. Returns raw bytes. Raises TTSError on failure."""
        ...

    @abstractmethod
    async def list_voices(self) -> list[dict]:
        """
        Return available voices.
        Each dict: {id, name, gender, locale, provider}
        IDs are raw (no namespace prefix).
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this provider can currently serve requests."""
        ...

    @abstractmethod
    def get_default_voice(self) -> str:
        """Raw default voice ID for this provider."""
        ...

    async def shutdown(self) -> None:
        """Optional cleanup on app shutdown."""

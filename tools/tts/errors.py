class TTSError(Exception):
    """
    Structured error raised by TTSTool and providers.

    Codes:
        "not_configured"       — provider missing required env vars
        "provider_unavailable" — host unreachable or 5xx
        "voice_not_found"      — voice_id does not exist in this provider
        "generation_failed"    — TTS engine error
        "timeout"              — request exceeded timeout
        "connection_error"     — TCP connect failed
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def __repr__(self) -> str:
        return f"TTSError(code={self.code!r}, msg={str(self)!r})"

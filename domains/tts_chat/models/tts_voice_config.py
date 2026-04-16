from pydantic import BaseModel
from typing import Optional


class TtsUserVoiceEntity(BaseModel):
    """DB mirror for tts_user_voice table."""
    id:           int
    twitch_id:    str
    twitch_login: str
    voice_id:     str
    voice_name:   str
    provider:     str
    created_at:   str
    updated_at:   str


class TtsSettingsEntity(BaseModel):
    """DB mirror for tts_settings table (single-row config)."""
    id:                  int
    enabled:             bool
    provider:            str
    host:                str
    port:                int
    default_voice:       str
    timeout_s:           int
    max_message_length:  int
    skip_commands:       bool
    skip_links:          bool
    sub_only:            bool
    cooldown_seconds:    int
    blocked_words:       str   # JSON array stored as text
    updated_at:          str

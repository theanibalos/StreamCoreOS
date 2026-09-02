from pydantic import BaseModel


class PlatformConnection(BaseModel):
    id: int
    platform: str
    channel_id: str
    channel_name: str
    enabled: bool
    chat_read_enabled: bool
    chat_write_enabled: bool
    moderation_enabled: bool
    capabilities: str
    created_at: str
    updated_at: str

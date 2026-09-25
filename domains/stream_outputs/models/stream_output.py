from typing import Optional
from pydantic import BaseModel


class StreamOutput(BaseModel):
    id: int
    name: str
    platform: str
    channel_id: str
    enabled: bool
    rtmp_url: Optional[str] = None
    stream_key_configured: bool = False
    stream_key_preview: Optional[str] = None
    status: str
    settings: dict
    created_at: str
    updated_at: str

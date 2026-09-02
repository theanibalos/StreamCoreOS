from typing import Optional
from pydantic import BaseModel


class YouTubeToken(BaseModel):
    id: Optional[int] = None
    channel_id: str
    channel_title: str
    access_token: str
    refresh_token: Optional[str] = None
    scopes: str = "[]"
    expires_at: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

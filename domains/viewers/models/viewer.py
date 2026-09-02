from typing import Optional
from pydantic import BaseModel


class Viewer(BaseModel):
    id: int
    global_user_id: str
    platform: str
    platform_user_id: str
    login: Optional[str] = None
    display_name: str
    avatar_url: Optional[str] = None
    points: int
    total_earned: int
    is_regular: bool
    first_seen: str
    last_seen: str

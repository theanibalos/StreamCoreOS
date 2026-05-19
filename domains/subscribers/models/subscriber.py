from typing import Optional
from pydantic import BaseModel


class Subscriber(BaseModel):
    id: int
    twitch_id: str
    login: str
    display_name: str
    tier: str
    is_prime: bool
    is_gift: bool
    cumulative_months: int
    streak_months: Optional[int]
    subscribed_at: str
    last_sub_at: str
    is_active: bool


class ViewerBits(BaseModel):
    id: int
    twitch_id: str
    login: str
    display_name: str
    bits_total: int
    last_cheer_at: str

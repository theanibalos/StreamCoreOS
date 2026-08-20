from typing import Optional
from pydantic import BaseModel


class ChatCommandEntity(BaseModel):
    """DB mirror of the chat_commands table."""
    id: int
    name: str
    response: str
    cooldown_s: int
    enabled: int
    created_at: str
    action: Optional[str] = None


class ChatLogEntity(BaseModel):
    """DB mirror of the chat_log table."""
    id: int
    channel: str
    user_id: str
    display_name: str
    message: str
    is_command: int
    timestamp: str

from pydantic import BaseModel
from typing import Optional

class WebhookEntity(BaseModel):
    id: Optional[int] = None
    name: str
    url: str
    method: str = "POST"
    headers: Optional[str] = None  # JSON string
    body_template: Optional[str] = None
    trigger_type: str  # "command" or "event"
    trigger_value: str  # command name or event bus pattern
    filter_field: Optional[str] = None
    filter_value: Optional[str] = None
    enabled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

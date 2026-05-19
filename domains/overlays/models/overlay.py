from pydantic import BaseModel
from datetime import datetime


class OverlayEntity(BaseModel):
    id: int | None = None
    name: str
    config: str = '{"elements":[]}'
    created_at: datetime | None = None
    updated_at: datetime | None = None

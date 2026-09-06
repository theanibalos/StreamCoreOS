from pydantic import BaseModel


class AIConfig(BaseModel):
    id:           int
    provider:     str
    endpoint_url: str
    model:        str
    updated_at:   str
    # api_key intentionally excluded — never expose it in responses

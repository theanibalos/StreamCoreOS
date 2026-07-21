from typing import Optional
from pydantic import BaseModel


class ModRuleEntity(BaseModel):
    """DB mirror of the mod_rules table."""
    id: int
    type: str       # word_filter | link_filter | caps_filter | spam_filter
    value: Optional[str]
    action: str     # timeout | ban | delete
    duration_s: Optional[int]
    enabled: int
    exempt_roles: str   # comma-separated: mod,vip,regular,sub


class ModLogEntity(BaseModel):
    """DB mirror of the mod_log table."""
    id: int
    twitch_id: str
    display_name: str
    action: str
    reason: str
    rule_id: Optional[int]
    created_at: str

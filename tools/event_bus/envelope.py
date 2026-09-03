"""
Enterprise Event Bus — Envelope & Tracing Models
=================================================
The Pydantic-native data contracts shared by EventBusTool, its drivers, and
anything that inspects the trace log. Split out of event_bus_tool.py (see
that file's module docstring for the full driver contract).
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Dict, List
from pydantic import BaseModel, Field, ConfigDict


class EventEnvelope(BaseModel):
    """The Universal Contract for any message traveling through the system."""
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event: str
    payload: Dict[str, Any]
    emitter: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    parent_id: Optional[str] = None
    correlation_id: Optional[str] = None
    reply_to: Optional[str] = None

    key: Optional[str] = None
    priority: Optional[int] = None
    delay: Optional[int] = None
    ttl: Optional[float] = None
    headers: Dict[str, Any] = Field(default_factory=dict)


class TraceNode(BaseModel):
    """Rich record for observability, capturing both publication and delivery events."""
    kind: str  # "published" or "delivered"
    envelope: EventEnvelope
    subscribers: List[str] = Field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    attempts: Optional[int] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TraceRecord(TraceNode):
    """Legacy compatibility alias for TraceNode."""
    pass


class SubOptions(BaseModel):
    """Configuration for a specific subscription."""
    retries: int = 0
    backoff: float = 0.5

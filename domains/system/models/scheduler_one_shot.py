"""
SchedulerOneShotEntity — Mirror of the `scheduler_one_shots` table.

RULE: This file contains ONE thing: the DB entity.
      Request/response schemas belong inside each plugin.
"""

from pydantic import BaseModel


class SchedulerOneShotEntity(BaseModel):
    job_id: str
    run_at_epoch: float
    event: str
    payload: str  # JSON-encoded dict, published as the event payload

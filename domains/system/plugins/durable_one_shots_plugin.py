"""
Durable one-shots — persistent scheduled events (Issue 19).

The scheduler tool keeps one-shots in memory BY DESIGN: an arbitrary callable
cannot survive a restart, and a tool never uses other tools. Durability is
composed HERE, in the plugin layer, where DI legitimately combines db +
scheduler + event_bus.

Any domain schedules without imports, via the bus:

    res = await self.bus.request("system.one_shot.schedule", {
        "run_at": "2026-06-10T15:00:00+00:00",   # ISO-8601, tz-aware
        "event": "jobs.welcome_email.due",
        "payload": {"user_id": 42},
    })
    job_id = res["data"]["job_id"]

    # the consumer (any domain, any replica — exactly one handler runs):
    await self.bus.subscribe("jobs.welcome_email.due", self.send_welcome)

    # cancel a pending one:
    await self.bus.request("system.one_shot.cancel", {"job_id": job_id})

Mechanics:
- Rows live in the scheduler_one_shots table (system domain migration).
- A cron job — every minute, which is also the precision: durable one-shots
  are for "in 1 hour", not "in 300ms" — publishes due events and deletes
  their rows. The cron fires only on the beat replica (SCHEDULER_ENABLED),
  while scheduling via bus works from ANY replica.
- One-shots missed while the beat was down fire (late) within a minute of boot.
- Delivery is at-least-once (publish, then delete the row): consuming
  handlers must be idempotent — already the bus contract.
"""

import json
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class ScheduleOneShotRequest(BaseModel):
    run_at: str = Field(min_length=1)  # ISO-8601 datetime, tz-aware recommended
    event: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)  # must be JSON-serializable
    job_id: Optional[str] = Field(default=None, min_length=1)


class CancelOneShotRequest(BaseModel):
    job_id: str = Field(min_length=1)


class DurableOneShotsPlugin(BasePlugin):
    def __init__(self, db, event_bus, scheduler, logger):
        self.db = db
        self.bus = event_bus
        self.scheduler = scheduler
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("system.one_shot.schedule", self.on_schedule)
        await self.bus.subscribe("system.one_shot.cancel", self.on_cancel)
        self.scheduler.add_job(
            "* * * * *", self.publish_due, job_id="system_durable_one_shots"
        )
        self.logger.info("[DurableOneShots] Scheduling service ready.")

    async def on_schedule(self, event) -> dict:
        try:
            req = ScheduleOneShotRequest(**event.payload)
            run_at = datetime.fromisoformat(req.run_at)
        except Exception:
            self.logger.error(
                f"[DurableOneShots] Invalid schedule request: {event.payload!r}"
            )
            return {"success": False, "error": "Invalid schedule request"}
        try:
            job_id = req.job_id or uuid.uuid4().hex
            # Re-using a job_id replaces the pending job (stable-id semantics).
            async with self.db.transaction() as tx:
                await tx.execute(
                    "DELETE FROM scheduler_one_shots WHERE job_id = $1", [job_id]
                )
                await tx.execute(
                    "INSERT INTO scheduler_one_shots (job_id, run_at_epoch, event, payload) "
                    "VALUES ($1, $2, $3, $4)",
                    [job_id, run_at.timestamp(), req.event, json.dumps(req.payload)],
                )
            return {"success": True, "data": {"job_id": job_id}}
        except Exception as e:
            self.logger.error(f"[DurableOneShots] Failed to store one-shot: {e}")
            return {"success": False, "error": "Could not schedule"}

    async def on_cancel(self, event) -> dict:
        try:
            req = CancelOneShotRequest(**event.payload)
        except Exception:
            return {"success": False, "error": "Invalid cancel request"}
        try:
            deleted = await self.db.execute(
                "DELETE FROM scheduler_one_shots WHERE job_id = $1", [req.job_id]
            )
            return {"success": True, "data": {"removed": bool(deleted)}}
        except Exception as e:
            self.logger.error(f"[DurableOneShots] Failed to cancel one-shot: {e}")
            return {"success": False, "error": "Could not cancel"}

    async def publish_due(self) -> None:
        """Cron callback (beat replica only): publish due rows, then delete them."""
        now = datetime.now().astimezone().timestamp()
        due = await self.db.query(
            "SELECT job_id, event, payload FROM scheduler_one_shots "
            "WHERE run_at_epoch <= $1 ORDER BY run_at_epoch",
            [now],
        )
        for row in due:
            await self.bus.publish(row["event"], json.loads(row["payload"]))
            await self.db.execute(
                "DELETE FROM scheduler_one_shots WHERE job_id = $1", [row["job_id"]]
            )
            self.logger.info(
                f"[DurableOneShots] Fired — id='{row['job_id']}' event='{row['event']}'"
            )

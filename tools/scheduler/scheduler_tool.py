"""
Scheduler Tool — Background Job Scheduling for MicroCoreOS
===========================================================

Wraps APScheduler (AsyncIOScheduler) to provide cron-style and one-shot
background jobs. Zero infrastructure required out of the box.

PUBLIC CONTRACT (what plugins use):
─────────────────────────────────────────────────────────────────────────

    # Recurring job — standard 5-field cron expression
    job_id = scheduler.add_job("0 * * * *", self.on_every_hour)
    job_id = scheduler.add_job("*/5 * * * *", self.send_digest, job_id="digest")

    # Recurring job — fixed interval, for the sub-minute rates cron cannot express
    job_id = scheduler.add_interval_job(1.0, self.sample_metrics)
    job_id = scheduler.add_interval_job(0.25, self.poll, job_id="poll", max_instances=4)

    # One-shot job — runs once at a specific datetime
    from datetime import datetime, timedelta, timezone
    run_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    job_id = scheduler.add_one_shot(run_at, self.send_welcome_email)

    # Remove a job (returns True if removed, False if not found)
    removed = scheduler.remove_job("digest")

    # Inspect scheduled jobs
    jobs = scheduler.list_jobs()
    # [{"id": "digest", "next_run": "2026-03-14 15:00:00+00:00", "trigger": "cron[...]"}]


CALLBACK SIGNATURES:
─────────────────────────────────────────────────────────────────────────

    # Sync callback — runs in APScheduler's executor
    def on_every_hour(self):
        ...

    # Async callback — runs in the asyncio event loop
    async def on_every_hour(self):
        await self.db.execute(...)


CRON EXPRESSION QUICK REFERENCE:
─────────────────────────────────────────────────────────────────────────

    "* * * * *"         — every minute
    "*/5 * * * *"       — every 5 minutes
    "0 * * * *"         — every hour (on the hour)
    "0 9 * * 1-5"       — 09:00 on weekdays
    "0 0 * * *"         — midnight every day
    "0 0 1 * *"         — midnight on the 1st of every month


SCALING (Issue 19 — the Elastic Monolith singleton pattern):
─────────────────────────────────────────────────────────────────────────

    With N replicas, every replica would fire every job N times. The fix is
    the Celery-beat pattern: the scheduler RUNS in exactly one replica.

    SCHEDULER_ENABLED=true   (default) — single instance / the "beat" replica.
    SCHEDULER_ENABLED=false  — worker replicas: plugins register their jobs
        normally (identical code everywhere), but the scheduler never starts,
        so nothing fires twice.

    Jobs must NOT do heavy work: they publish an event and return —
    the workers consume it with the bus's group semantics, so exactly ONE
    worker across the fleet executes it:

        # In the plugin (runs in every replica; fires only in the beat one):
        self.scheduler.add_job("0 3 * * *", self.emit_nightly, job_id="nightly_report")

        async def emit_nightly(self):
            await self.bus.publish("jobs.nightly_report.due", {})

        # Worker side (any replica — group derived automatically):
        await self.bus.subscribe("jobs.nightly_report.due", self.run_report)

    Cron jobs need NO persistence: they re-register on every boot via
    on_boot() with a stable job_id (replace_existing avoids duplicates).
    KNOWN LIMIT (by design): one-shots (add_one_shot) live in memory — an
    arbitrary callable cannot survive a restart, and a tool never uses other
    tools. Durable one-shots are composed in the PLUGIN layer (db + scheduler
    + bus via DI): see extras/available_domains/scheduler/plugins/durable_one_shots_plugin.py,
    usable from any domain via the bus ("scheduler.one_shot.schedule").

REPLACEMENT STANDARD (swap without changing plugins):
─────────────────────────────────────────────────────────────────────────

    To replace with Celery beat or any other scheduler:
    1. Create tools/{name}/{name}_tool.py
    2. Set name = "scheduler"                    ← same injection key
    3. Implement the 5 public methods:
         add_job(cron_expr, callback, job_id?) → str
         add_interval_job(seconds, callback, job_id?, ...) → str
         add_one_shot(run_at, callback, job_id?) → str
         remove_job(job_id) → bool
         list_jobs() → list[dict]
    4. Honor SCHEDULER_ENABLED (jobs register everywhere, fire in one place).
    Plugins do not change.

    LIMIT OF THIS CONTRACT: every method above takes a Python callable, so it
    is implementable only by a scheduler running IN THIS PROCESS (APScheduler,
    `schedule`, a bare asyncio loop). Celery beat and every other distributed
    beat dispatch task NAMES to a broker for other processes to run, and a
    bound method does not cross a process boundary. Distributed scheduling is
    reached the other way — by scheduling an EVENT rather than a function:
    "scheduler.one_shot.schedule" on the bus, where everything crossing the
    boundary is JSON. See durable_one_shots_plugin.py.
"""

import functools
import inspect
import os
import uuid
from datetime import datetime
from typing import Callable, Optional
from microcoreos import BaseTool
from microcoreos import current_identity_var


def _callback_identity(callback: Callable) -> str:
    """Identity for events/logs a job produces: '<domain>.<Class>.<method>'
    when the callback is a plugin's bound method (mirrors the event bus's
    subscriber naming), module-qualified fallback otherwise."""
    owner = getattr(callback, "__self__", None)
    if owner is not None:
        base = getattr(owner, "_identity", None)
        if not base:
            cls = owner.__class__
            base = f"{cls.__module__}.{cls.__name__}"
        return f"{base}.{callback.__name__}"
    module = getattr(callback, "__module__", None) or "anonymous"
    return f"{module}.{getattr(callback, '__qualname__', 'anonymous')}"


def _with_identity(callback: Callable) -> Callable:
    """Jobs fire from APScheduler's own context, where current_identity_var
    is unset — without this wrapper everything a job publishes or logs is
    attributed to the anonymous default instead of the owning plugin."""
    identity = _callback_identity(callback)
    if inspect.iscoroutinefunction(callback):
        @functools.wraps(callback)
        async def async_wrapper():
            token = current_identity_var.set(identity)
            try:
                return await callback()
            finally:
                current_identity_var.reset(token)
        return async_wrapper

    @functools.wraps(callback)
    def sync_wrapper():
        token = current_identity_var.set(identity)
        try:
            return callback()
        finally:
            current_identity_var.reset(token)
    return sync_wrapper


class SchedulerTool(BaseTool):
    """
    Background job scheduler for MicroCoreOS.

    Uses APScheduler's AsyncIOScheduler as the default backend,
    which runs jobs directly in the asyncio event loop — no threads,
    no external processes, no infrastructure dependencies.

    Supports both async and sync callbacks transparently.
    """

    @property
    def name(self) -> str:
        return "scheduler"

    # ─── LIFECYCLE ──────────────────────────────────────────────

    def __init__(self) -> None:
        self._scheduler = None
        # Issue 19: the scheduler fires in exactly ONE replica (beat role).
        self._enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").strip().lower() == "true"

    def setup(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.events import EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES
            self._scheduler = AsyncIOScheduler()
            self._scheduler.add_listener(
                self._on_run_dropped, EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES
            )
            print("[Scheduler] APScheduler initialized.")
        except ImportError:
            raise RuntimeError(
                "[Scheduler] APScheduler is required. "
                "Install with: uv add 'apscheduler>=3.10,<4'"
            )

    async def on_boot_complete(self, container) -> None:
        """Start the scheduler after all plugins have registered their jobs."""
        job_count = len(self._scheduler.get_jobs())
        if not self._enabled:
            print(f"[Scheduler] SCHEDULER_ENABLED=false — worker replica: "
                  f"{job_count} job(s) registered but NOT started (the beat replica fires them).")
            return
        self._scheduler.start()
        print(f"[Scheduler] Started — {job_count} job(s) registered.")

    def _on_run_dropped(self, event) -> None:
        """
        APScheduler discards runs without raising: one past its misfire grace
        time (EVENT_JOB_MISSED) or one that would exceed max_instances
        (EVENT_JOB_MAX_INSTANCES) never reaches the callback and returns no
        error to anybody. Unlogged, a job firing less often than it was
        configured to is indistinguishable from one firing correctly.
        """
        from apscheduler.events import EVENT_JOB_MAX_INSTANCES

        reason = ("overlapped a still-running execution (max_instances)"
                  if event.code == EVENT_JOB_MAX_INSTANCES
                  else "was later than its misfire_grace_time")
        # JobExecutionEvent carries scheduled_run_time; JobSubmissionEvent, a list.
        when = getattr(event, "scheduled_run_time", None) or getattr(event, "scheduled_run_times", None)
        print(f"[Scheduler] Run DROPPED — id={event.job_id!r} {reason}, scheduled for {when}")

    def shutdown(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            print("[Scheduler] Stopped.")

    # ─── PUBLIC API ─────────────────────────────────────────────

    def add_job(
        self,
        cron_expr: str,
        callback: Callable,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Schedule a recurring job using a standard 5-field cron expression.

        Parameters:
            cron_expr:  Standard cron string, e.g. "0 * * * *" (every hour).
            callback:   Sync or async callable. Called with no arguments.
            job_id:     Optional stable ID. Auto-generated if omitted.
                        Providing a stable ID allows the job to be removed by name
                        and prevents duplicates on hot-reload.

        Returns: the job_id string.

        Examples:
            scheduler.add_job("*/5 * * * *", self.flush_cache)
            scheduler.add_job("0 9 * * 1-5", self.send_digest, job_id="morning_digest")
        """
        from apscheduler.triggers.cron import CronTrigger

        job_id = job_id or uuid.uuid4().hex
        self._scheduler.add_job(
            _with_identity(callback),
            trigger=CronTrigger.from_crontab(cron_expr),
            id=job_id,
            replace_existing=True,
        )
        print(f"[Scheduler] Job registered — id={job_id!r} cron={cron_expr!r}")
        return job_id

    def add_interval_job(
        self,
        seconds: float,
        callback: Callable,
        job_id: Optional[str] = None,
        *,
        minutes: float = 0,
        hours: float = 0,
        max_instances: int = 1,
        coalesce: bool = True,
        misfire_grace_time: Optional[int] = 1,
    ) -> str:
        """
        Schedule a recurring job on a fixed interval.

        Cron's smallest unit is the minute, so anything faster — and any rate
        that is not a whole number of minutes — has to be an interval.

        Parameters:
            seconds:  Interval in seconds; accepts fractions (0.25 = 4x/second).
                      Combined additively with minutes and hours.
            callback: Sync or async callable. Called with no arguments.
            job_id:   Optional stable ID. Auto-generated if omitted.

        The last three mirror APScheduler's job defaults and matter as the
        interval approaches the callback's own duration:

            max_instances:      concurrent runs allowed. At 1, a run starting
                                while the previous one is still going is
                                DROPPED, not queued.
            coalesce:           collapse several missed runs into one.
            misfire_grace_time: seconds late a run may start; past that it is
                                DROPPED. None means run it however late.

        Dropped runs raise nothing — they are reported by _on_run_dropped().
        A job that must not skip needs max_instances above 1, a callback
        faster than the interval, or both.

        Returns: the job_id string.

        Examples:
            scheduler.add_interval_job(1.0, self.sample_metrics)
            scheduler.add_interval_job(0.25, self.poll, job_id="poll", max_instances=4)
            scheduler.add_interval_job(0, self.hourly, minutes=90)
        """
        from apscheduler.triggers.interval import IntervalTrigger

        if seconds <= 0 and minutes <= 0 and hours <= 0:
            raise ValueError(
                "add_interval_job: interval must be positive — "
                f"got seconds={seconds}, minutes={minutes}, hours={hours}"
            )

        job_id = job_id or uuid.uuid4().hex
        self._scheduler.add_job(
            _with_identity(callback),
            trigger=IntervalTrigger(seconds=seconds, minutes=minutes, hours=hours),
            id=job_id,
            replace_existing=True,
            max_instances=max_instances,
            coalesce=coalesce,
            misfire_grace_time=misfire_grace_time,
        )
        print(
            f"[Scheduler] Interval job registered — id={job_id!r} "
            f"every {seconds}s+{minutes}m+{hours}h max_instances={max_instances}"
        )
        return job_id

    def add_one_shot(
        self,
        run_at: datetime,
        callback: Callable,
        job_id: Optional[str] = None,
    ) -> str:
        """
        Schedule a one-time job to run at a specific datetime.

        Parameters:
            run_at:    datetime (timezone-aware recommended) when the job should run.
            callback:  Sync or async callable. Called with no arguments.
            job_id:    Optional stable ID. Auto-generated if omitted.

        Returns: the job_id string.

        Example:
            from datetime import datetime, timedelta, timezone
            run_at = datetime.now(timezone.utc) + timedelta(hours=1)
            scheduler.add_one_shot(run_at, self.send_welcome_email)
        """
        from apscheduler.triggers.date import DateTrigger

        job_id = job_id or uuid.uuid4().hex
        self._scheduler.add_job(
            _with_identity(callback),
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            replace_existing=True,
        )
        print(f"[Scheduler] One-shot job registered — id={job_id!r} run_at={run_at}")
        return job_id

    def remove_job(self, job_id: str) -> bool:
        """
        Remove a scheduled job by ID.

        Returns True if the job was found and removed, False otherwise.
        Safe to call even if the job has already run or never existed.
        """
        try:
            self._scheduler.remove_job(job_id)
            print(f"[Scheduler] Job removed — id={job_id!r}")
            return True
        except Exception:
            return False

    def list_jobs(self) -> list:
        """
        Return a snapshot of all currently scheduled jobs.

        Each entry: {"id": str, "next_run": str | None, "trigger": str}
        """
        return [
            {
                "id": job.id,
                # Jobs added before the scheduler starts (or in a worker
                # replica, where it never starts) have no next_run_time yet.
                "next_run": str(job.next_run_time) if getattr(job, "next_run_time", None) else None,
                "trigger": str(job.trigger),
            }
            for job in self._scheduler.get_jobs()
        ]

    # ─── INTERFACE DESCRIPTION ──────────────────────────────────

    def get_interface_description(self) -> str:
        return """
        Scheduler Tool (scheduler):
        - PURPOSE: Background job scheduling — cron-style recurring jobs and one-shot timed jobs.
          Backed by APScheduler AsyncIOScheduler. Zero infrastructure required.
          Supports both async and sync callbacks transparently.
        - CAPABILITIES:
            - add_job(cron_expr: str, callback, job_id?: str) -> str:
                Schedule a recurring job with a 5-field cron expression.
                e.g. "*/5 * * * *" = every 5 min, "0 9 * * 1-5" = weekdays at 09:00.
                Returns job_id (auto-generated if not provided).
                Providing a stable job_id prevents duplicates on restart.
            - add_interval_job(seconds: float, callback, job_id?: str, *, minutes, hours,
                               max_instances=1, coalesce=True, misfire_grace_time=1) -> str:
                Schedule a recurring job on a fixed interval. Use this for sub-minute
                rates, which a 5-field cron expression cannot express (its unit is the
                minute). seconds accepts fractions: 0.25 = 4x/second.
                At max_instances=1 a run that overlaps the previous one is DROPPED, and
                a run later than misfire_grace_time is DROPPED — silently, as far as the
                callback is concerned. Both are logged as "Run DROPPED". Raise
                max_instances if the job must not skip.
            - add_one_shot(run_at: datetime, callback, job_id?: str) -> str:
                Schedule a one-time job at a specific datetime (timezone-aware).
                Returns job_id. IN-MEMORY: lost if the process restarts before firing.
                For one-shots that must survive restarts, publish to the bus:
                "scheduler.one_shot.schedule" (durable scheduling service — install extras/available_domains/scheduler).
            - remove_job(job_id: str) -> bool:
                Remove a job by ID. Returns True if removed, False if not found.
            - list_jobs() -> list[dict]:
                Snapshot of all scheduled jobs: [{id, next_run, trigger}].
        - REGISTER IN on_boot(): jobs are collected during on_boot(), scheduler starts
          in on_boot_complete() after all plugins have registered.
        - SCALING (N replicas): set SCHEDULER_ENABLED=false in worker replicas — jobs
          register everywhere but fire only in the single "beat" replica. Jobs should
          publish an event to the bus and return; workers consume it (group semantics
          guarantee exactly one execution across the fleet). Do heavy work in the
          worker, never in the job callback.
        - SWAP: replace with Celery beat by creating a new tool with name = "scheduler"
          and the same 4-method API. Plugins do not change.
        """

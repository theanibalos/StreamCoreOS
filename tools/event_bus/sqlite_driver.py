"""
SQLite Driver — Durable local transport for the Event Bus (Issue 31)
====================================================================

Broker-grade delivery guarantees for the single-process monolith: delays,
retries-in-flight and unhandled events SURVIVE a process death. Pure
transport: retries, DLQ, RPC, tracing and auto-unsubscribe stay in the Bus,
exactly as the replacement standard in event_bus_tool.py prescribes.

ACTIVATION (zero code changes):
─────────────────────────────────────────────────────────────────
    EVENT_BUS_DRIVER=sqlite uv run main.py

The elastic ladder gains a rung:
    in_process (fast, ephemeral) → sqlite (durable, one node)
    → redis_streams / rabbitmq / kafka (distributed)

STORAGE — its own database file, NEVER the business one:
─────────────────────────────────────────────────────────────────
The queue lives in EVENT_BUS_SQLITE_PATH (default "event_bus_queue.db"),
owned by this driver with its own connection — it does NOT use the db tool,
exactly like the Redis driver owns its client. Sharing the business file
would buy nothing (SQLite transactions are per-connection → no atomicity)
and cost plenty (single-writer lock contention with business INSERTs).
This file is the embedded equivalent of a broker's log: when the transport
is swapped to Kafka, it disappears with the driver. Commit→publish atomicity
remains the Outbox's job (ROADMAP Issue 28).

TRANSPORT MAPPING:
─────────────────────────────────────────────────────────────────
    subscribe(group="g")  → registered group: every publish fans out one
        durable delivery row per matching group (RabbitMQ queue model; groups
        are matched at publish time). Multiple callbacks on one group are
        COMPETING consumers: rows are claimed atomically, exactly one gets it.
    subscribe(group=None) → ephemeral, in-memory only (broadcast: RPC
        replies, broadcast=True). Deliberately NOT persisted — a reply or
        a cache-invalidation replayed after reboot would be wrong.
    delay                 → stored as due_at (now + delay): DURABLE — a
        pending delay fires after a restart, at its stored due time.
    key                   → the ORDERING UNIT, as on every other transport.
        Same-key publishes reach this queue in call order (the Bus chains the
        hand-offs); across keys nothing is promised. Rows are still claimed
        ORDER BY id, so a single consumer sees each key's sequence intact.
        This used to read "no-op, the queue is totally ordered" — it was not:
        publish() is fire-and-forget and the hand-offs raced, so the row order
        was the order threads won in. See docs/internal/TECH_DEBT.md item 4.
    priority              → accepted but a no-op (no priority lanes) — same
        degradation as Redis Streams.
    ttl                   → enforced Bus-side at delivery (age check), so an
        expired event still produces its "ttl_expired" trace node.

CONFIGURATION (env vars, read in __init__, zero I/O):
─────────────────────────────────────────────────────────────────
    EVENT_BUS_SQLITE_PATH         default "event_bus_queue.db"
    EVENT_BUS_SQLITE_POLL_MS      default "25" — idle poll interval. In-process
        publishes wake readers instantly; polling only picks up due delays
        and reboot backlogs.
    EVENT_BUS_SQLITE_MAXLEN       default "10000" — approximate cap of queued
        rows per (event, group); oldest pruned (like the Redis stream MAXLEN).
    EVENT_BUS_SQLITE_SYNCHRONOUS  default "FULL" — the honest durability
        setting (fsync per commit). "NORMAL" trades a small crash window for
        throughput. This is the documented cost of the durable rung.

DELIVERY GUARANTEE: at-least-once. A row is deleted only AFTER the handler
(including Bus-side retries/DLQ) finishes — rows claimed by a process that
died are reset to pending at next boot and redelivered. Handlers must be
idempotent (already required by the Bus contract). The queue file is
per-instance: this driver is the durable rung of the SINGLE-process monolith;
for N replicas use a distributed driver.
"""

import os
import time
import sqlite3
import asyncio
import threading
from typing import Callable, Optional

from tools.event_bus.event_bus_tool import EventBusDriver

_SYNC_MODES = {"OFF", "NORMAL", "FULL", "EXTRA"}


class _Subscription:
    """One consumer: (event, group, callback). Durable ones own a reader task."""

    def __init__(self, event: str, group: Optional[str], callback: Callable,
                 ephemeral: bool):
        self.event = event
        self.group = group
        self.callback = callback
        self.ephemeral = ephemeral
        self.task: Optional[asyncio.Task] = None


class SQLiteDriver(EventBusDriver):
    # delay=native: stored as due_at, fires after a restart (see header).
    # (Ephemeral broadcasts still sleep in-process — they never survive
    # a reboot by design, so there is nothing to persist.)
    capabilities = {"delay": "native", "retries": "in_bus", "dlq": "in_bus"}

    PRUNE_EVERY = 128  # publishes between approximate MAXLEN prunes

    def __init__(self) -> None:
        self._path: str = os.getenv("EVENT_BUS_SQLITE_PATH", "event_bus_queue.db")
        self._poll_s: float = int(os.getenv("EVENT_BUS_SQLITE_POLL_MS", "25")) / 1000.0
        self._maxlen: int = int(os.getenv("EVENT_BUS_SQLITE_MAXLEN", "10000"))
        sync = os.getenv("EVENT_BUS_SQLITE_SYNCHRONOUS", "FULL").upper()
        if sync not in _SYNC_MODES:
            # Falling back is right — FULL is the durable one — but doing it
            # quietly leaves someone who asked for NORMAL wondering why writes
            # are slow. SQLite is no help here: it accepts an unknown value and
            # settles on NORMAL without erroring.
            print(f"[SQLiteDriver] EVENT_BUS_SQLITE_SYNCHRONOUS={sync!r} is not one of "
                  f"{sorted(_SYNC_MODES)} — using FULL.")
        self._synchronous: str = sync if sync in _SYNC_MODES else "FULL"
        self._conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()
        self._subs: list[_Subscription] = []
        self._wakeup = asyncio.Event()
        self._publish_count = 0

    # ─── LIFECYCLE ────────────────────────────────────────

    async def setup(self) -> None:
        def _open() -> sqlite3.Connection:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA synchronous={self._synchronous}")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deliveries ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  event TEXT NOT NULL,"        # the SUBSCRIPTION key
                "  grp TEXT NOT NULL,"
                "  envelope TEXT NOT NULL,"
                "  due_at REAL NOT NULL,"
                "  status TEXT NOT NULL DEFAULT 'pending',"  # pending | processing
                "  created_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_deliveries_ready "
                "ON deliveries (event, grp, status, due_at)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS groups ("
                "  event TEXT NOT NULL, grp TEXT NOT NULL, PRIMARY KEY (event, grp))"
            )
            # Crash recovery: this process just started, so nothing can be
            # legitimately in flight — anything 'processing' belonged to a
            # dead run and must redeliver (at-least-once).
            conn.execute("UPDATE deliveries SET status='pending' WHERE status='processing'")
            conn.commit()
            return conn

        self._conn = await asyncio.to_thread(_open)
        print(f"[System] SQLiteDriver: Durable local transport ready ({self._path}).")

    async def shutdown(self) -> None:
        for sub in self._subs:
            await self._stop_subscription(sub)
        self._subs.clear()
        if self._conn is not None:
            conn, self._conn = self._conn, None
            # A cancelled to_thread task can return before its worker thread
            # actually stops (cancellation can't interrupt a running thread),
            # so a reader's _ack/_claim_one may still be mid-execute here.
            # Route close() through the same lock so it waits its turn
            # instead of racing the connection out from under that thread.
            def _close():
                with self._db_lock:
                    conn.close()
            await asyncio.to_thread(_close)

    # ─── TRANSPORT: publish ───────────────────────────────

    async def publish(self, envelope) -> None:
        delay = envelope.delay if envelope.delay and envelope.delay > 0 else 0
        due_at = time.time() + delay
        raw = envelope.model_dump_json()

        def _stage() -> list:
            with self._db_lock:
                matched = self._conn.execute(
                    "SELECT event, grp FROM groups WHERE event = ?",
                    (envelope.event,),
                ).fetchall()
                for sub_event, grp in matched:
                    self._conn.execute(
                        "INSERT INTO deliveries (event, grp, envelope, due_at, status, created_at) "
                        "VALUES (?, ?, ?, ?, 'pending', ?)",
                        (sub_event, grp, raw, due_at, time.time()),
                    )
                self._conn.commit()
                return matched

        matched = await asyncio.to_thread(_stage)
        if matched:
            self._wakeup.set()
            self._publish_count += 1
            if self._maxlen > 0 and self._publish_count % self.PRUNE_EVERY == 0:
                await asyncio.to_thread(self._prune, matched)

        # Ephemeral broadcasts are in-memory by design (never survive reboot).
        if delay:
            await asyncio.sleep(delay)
        for sub in [s for s in self._subs
                    if s.ephemeral and s.event == envelope.event]:
            asyncio.create_task(self._deliver_hook(envelope, sub.callback))

    def _prune(self, matched: list) -> None:
        with self._db_lock:
            for sub_event, grp in matched:
                self._conn.execute(
                    "DELETE FROM deliveries WHERE event=? AND grp=? AND id NOT IN ("
                    "  SELECT id FROM deliveries WHERE event=? AND grp=? "
                    "  ORDER BY id DESC LIMIT ?)",
                    (sub_event, grp, sub_event, grp, self._maxlen),
                )
            self._conn.commit()

    # ─── TRANSPORT: subscribe / readers ───────────────────

    async def subscribe(self, event_name: str, group: Optional[str], callback: Callable):
        ephemeral = group is None
        sub = _Subscription(event_name, group, callback, ephemeral)

        if not ephemeral:
            # Registering the group is the "$" moment: fan-out starts with the
            # NEXT publish; a group that already existed (previous run) drains
            # its backlog — that is exactly the reboot-redelivery guarantee.
            def _register():
                with self._db_lock:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO groups (event, grp) VALUES (?, ?)",
                        (event_name, group),
                    )
                    self._conn.commit()

            await asyncio.to_thread(_register)
            sub.task = asyncio.create_task(self._reader(sub))

        self._subs.append(sub)

    def _claim_one(self, sub: _Subscription):
        """Atomic claim: competing consumers of a group never share a row."""
        with self._db_lock:
            row = self._conn.execute(
                "UPDATE deliveries SET status='processing' WHERE id = ("
                "  SELECT id FROM deliveries WHERE event=? AND grp=? "
                "  AND status='pending' AND due_at<=? ORDER BY id LIMIT 1) "
                "RETURNING id, envelope",
                (sub.event, sub.group, time.time()),
            ).fetchone()
            self._conn.commit()
            return row

    def _ack(self, row_id: int) -> None:
        with self._db_lock:
            self._conn.execute("DELETE FROM deliveries WHERE id=?", (row_id,))
            self._conn.commit()

    async def _reader(self, sub: _Subscription) -> None:
        while True:
            row = await asyncio.to_thread(self._claim_one, sub)
            if row is None:
                # Idle: in-process publishes wake us instantly; the timeout
                # only matters for due delays and nothing-published lulls.
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_s)
                except asyncio.TimeoutError:
                    pass
                continue

            row_id, raw = row
            try:
                envelope = self._envelope_cls.model_validate_json(raw)
                delivery = await self._deliver_hook(envelope, sub.callback)
                if delivery is not None:
                    # Ack (DELETE) only AFTER the handler and its Bus-side
                    # retries finish: a process dying here leaves the row
                    # 'processing', reset to pending at next boot (redelivery).
                    # shield(): a handler may unsubscribe US (poisoned-handler
                    # escalation) — cancelling this reader must not cancel the
                    # in-flight delivery that triggered it.
                    await asyncio.shield(delivery)
            except asyncio.CancelledError:
                raise  # row stays 'processing' → redelivered next boot
            except Exception as e:
                # Corrupt row: never let it kill the reader (acked below).
                print(f"[SQLiteDriver] ⚠️ Undeliverable row {row_id} on {sub.event}: {e}")
            await asyncio.to_thread(self._ack, row_id)

    # ─── TRANSPORT: unsubscribe ───────────────────────────

    async def unsubscribe(self, event_name: str, callback: Callable):
        for sub in [s for s in self._subs if s.event == event_name and s.callback == callback]:
            await self._stop_subscription(sub)
            self._subs.remove(sub)

    async def unsubscribe_all(self, callback: Callable):
        for sub in [s for s in self._subs if s.callback == callback]:
            await self._stop_subscription(sub)
            self._subs.remove(sub)

    async def _stop_subscription(self, sub: _Subscription) -> None:
        # The group registration is kept on purpose (Redis parity: a durable
        # group outlives its consumers, so a resubscribing plugin drains what
        # accumulated while it was away).
        if sub.task is not None:
            sub.task.cancel()
            try:
                await sub.task
            except (asyncio.CancelledError, Exception):
                pass

    # ─── OBSERVABILITY ────────────────────────────────────

    def get_status(self, name_resolver: Callable) -> dict:
        status: dict = {}
        for sub in self._subs:
            status.setdefault(sub.event, []).append(name_resolver(sub.callback))
        return status

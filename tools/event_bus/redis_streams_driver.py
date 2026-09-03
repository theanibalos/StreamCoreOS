"""
Redis Streams Driver — Distributed transport for the Event Bus
==============================================================

First production EventBusDriver (Issue 18). Pure transport: retries, DLQ,
RPC, tracing and auto-unsubscribe stay in the Bus, exactly as the
replacement standard in event_bus_tool.py prescribes.

ACTIVATION (zero code changes):
─────────────────────────────────────────────────────────────────
    EVENT_BUS_DRIVER=redis_streams uv run main.py

With N replicas pointing at the same Redis, events published by one
instance reach subscribers in all instances — and `group=` subscriptions
become real exactly-one-consumer groups ACROSS replicas (the Elastic
Monolith scaling pattern).

TRANSPORT MAPPING:
─────────────────────────────────────────────────────────────────
    event "user.created"  → stream  "bus:user.created" (XADD, capped MAXLEN ~10000)
    subscribe(group="g")   → durable consumer group "g": Redis delivers each
        message to exactly one consumer in the group across the WHOLE fleet.
        The Bus auto-derives stable groups from the callback identity, so this
        is the normal path for every business subscription.
    subscribe(group=None)  → ephemeral consumer group "_bcast_<uuid>"
        starting at "$" (broadcast: each subscriber sees every NEW message;
        destroyed on unsubscribe). Only broadcast subscriptions reach the
        driver without a group: RPC replies, broadcast=True.
    delay                  → NATIVE (capability claim, Issue 30): the envelope
        is parked in the sorted set "bus:__delayed__" scored by due time and
        promoted to its streams by an atomic Lua script that every replica
        polls — the delay is REDIS-persisted and survives a publisher crash
        ("__delayed__" is therefore a reserved event name).
    key / priority         → accepted but no-ops: a stream is already totally
        ordered, and Streams have no message priority
    ttl                    → enforced Bus-side at delivery (age check)

CONFIGURATION (env vars):
─────────────────────────────────────────────────────────────────
    REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_PASSWORD / REDIS_CONNECT_TIMEOUT
    (same variables as the Redis state tool — one Redis serves both)
    EVENT_BUS_STREAM_MAXLEN   default "10000" (approximate cap per stream)
    EVENT_BUS_DELAY_POLL_MS   default "500" — how often each replica promotes
        due delayed envelopes (delivery accuracy of delay=n is ± this)
    EVENT_BUS_CLAIM_IDLE_MS   default "60000" — pending entries idle longer
        than this are reclaimed from dead consumers. RAISE it above the
        worst-case handler duration (including Bus retries/backoff): a live
        handler slower than this is reclaimed and runs TWICE.

DELIVERY GUARANTEE: at-least-once. A message is XACKed only AFTER the handler
(including Bus-side retries/DLQ) finishes — if the replica dies mid-handler,
the message stays pending and another consumer of the same group reclaims it
via XAUTOCLAIM (idle > EVENT_BUS_CLAIM_IDLE_MS). Handlers must be idempotent
(already required by the Bus contract: a crash after the handler but before
the ack = redelivery).
"""

import os
import time
import uuid
import asyncio
import redis.asyncio as aioredis
from redis import exceptions as redis_exceptions
from typing import Callable, Optional
from microcoreos import ToolUnavailableError
from tools.event_bus.event_bus_tool import EventBusDriver


class EventBusConnectionError(ToolUnavailableError):
    """Redis broker unreachable — ToolProxy marks the bus DEAD immediately."""
    pass


class _Subscription:
    """One reader loop: (event, callback) consuming a stream via a consumer group."""

    def __init__(self, event: str, stream: str, group: str, consumer: str,
                 callback: Callable, ephemeral: bool):
        self.event = event
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.callback = callback
        self.ephemeral = ephemeral  # broadcast groups are destroyed on unsubscribe
        self.task: Optional[asyncio.Task] = None


class RedisStreamsDriver(EventBusDriver):
    STREAM_PREFIX = "bus:"
    DELAYED_KEY = "bus:__delayed__"

    capabilities = {"delay": "native", "retries": "in_bus", "dlq": "in_bus"}

    # Atomically promotes due envelopes: ZSET → the event's stream.
    # Atomic per script run (Redis is single-threaded), so N replicas can poll
    # concurrently without double-promoting, and a replica dying mid-poll
    # cannot lose an envelope (it is either still parked or fully promoted).
    # KEYS[1]=delayed zset, ARGV[1]=now, ARGV[2]=stream prefix, ARGV[3]=maxlen
    _PROMOTE_LUA = """
        local due = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, 100)
        for i, raw in ipairs(due) do
            local env = cjson.decode(raw)
            redis.call('XADD', ARGV[2] .. env.event, 'MAXLEN', '~', ARGV[3], '*', 'json', raw)
            redis.call('ZREM', KEYS[1], raw)
        end
        return #due
    """

    def __init__(self) -> None:
        self._host: str = os.getenv("REDIS_HOST", "localhost")
        self._port: int = int(os.getenv("REDIS_PORT", "6379"))
        self._db: int = int(os.getenv("REDIS_DB", "0"))
        self._password: str = os.getenv("REDIS_PASSWORD", "")
        self._connect_timeout: float = float(os.getenv("REDIS_CONNECT_TIMEOUT", "5"))
        self._maxlen: int = int(os.getenv("EVENT_BUS_STREAM_MAXLEN", "10000"))
        self._delay_poll_s: float = int(os.getenv("EVENT_BUS_DELAY_POLL_MS", "500")) / 1000.0
        self._claim_idle_ms: int = int(os.getenv("EVENT_BUS_CLAIM_IDLE_MS", "60000"))
        self._redis: aioredis.Redis | None = None
        self._subs: list[_Subscription] = []
        self._promoter_task: asyncio.Task | None = None

    # ─── LIFECYCLE ────────────────────────────────────────

    async def setup(self) -> None:
        print(f"[System] RedisStreamsDriver: Connecting to {self._host}:{self._port}/{self._db}...")
        self._redis = aioredis.Redis(
            host=self._host,
            port=self._port,
            db=self._db,
            password=self._password or None,
            socket_connect_timeout=self._connect_timeout,
            decode_responses=True,
        )
        try:
            await self._redis.ping()
        except (redis_exceptions.RedisError, OSError) as e:
            raise EventBusConnectionError(
                f"Cannot connect to Redis broker at {self._host}:{self._port}/{self._db}: {e}"
            ) from e
        # Every replica promotes: delayed envelopes left by a dead publisher
        # still fire as long as ANY replica is alive (the native-delay claim).
        self._promoter_task = asyncio.create_task(self._promote_delayed())
        print("[System] RedisStreamsDriver: Distributed transport ready.")

    async def shutdown(self) -> None:
        if self._promoter_task is not None:
            self._promoter_task.cancel()
            try:
                await self._promoter_task
            except (asyncio.CancelledError, Exception):
                pass
            self._promoter_task = None
        for sub in self._subs:
            await self._stop_subscription(sub)
        self._subs.clear()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ─── TRANSPORT: publish ───────────────────────────────

    async def publish(self, envelope) -> None:
        if envelope.delay and envelope.delay > 0:
            # Native delay: park it in Redis NOW (crash-safe), scored by due
            # time; the promoter loop moves it to the streams when due.
            try:
                await self._redis.zadd(
                    self.DELAYED_KEY,
                    {envelope.model_dump_json(): time.time() + envelope.delay},
                )
            except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as e:
                raise EventBusConnectionError(f"Redis broker unreachable: {e}") from e
            return
        fields = {"json": envelope.model_dump_json()}
        try:
            await self._redis.xadd(f"{self.STREAM_PREFIX}{envelope.event}", fields,
                                   maxlen=self._maxlen, approximate=True)
        except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as e:
            raise EventBusConnectionError(f"Redis broker unreachable: {e}") from e

    async def _promote_delayed(self) -> None:
        """Poll loop: run the atomic promotion script every DELAY_POLL_MS."""
        while True:
            try:
                await self._redis.eval(
                    self._PROMOTE_LUA, 1, self.DELAYED_KEY,
                    time.time(), self.STREAM_PREFIX, self._maxlen,
                )
            except asyncio.CancelledError:
                raise
            except redis_exceptions.RedisError:
                pass  # broker hiccup: next tick retries, envelopes stay parked
            await asyncio.sleep(self._delay_poll_s)

    # ─── TRANSPORT: subscribe / readers ───────────────────

    async def subscribe(self, event_name: str, group: Optional[str], callback: Callable):
        stream = f"{self.STREAM_PREFIX}{event_name}"
        ephemeral = group is None
        group_name = group if group is not None else f"_bcast_{uuid.uuid4().hex[:12]}"
        consumer = uuid.uuid4().hex[:12]

        # "$" = deliver only messages published AFTER this point — same
        # semantics as the in-process driver (no replay on subscribe).
        try:
            await self._redis.xgroup_create(stream, group_name, id="$", mkstream=True)
        except redis_exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        sub = _Subscription(event_name, stream, group_name, consumer, callback, ephemeral)
        sub.task = asyncio.create_task(self._reader(sub))
        self._subs.append(sub)

    # How often each reader looks for entries abandoned by a dead consumer
    # (durable groups only). The idle threshold itself is EVENT_BUS_CLAIM_IDLE_MS.
    CLAIM_EVERY_S = 30.0

    async def _reader(self, sub: _Subscription) -> None:
        last_claim = time.monotonic()
        while True:
            try:
                response = await self._redis.xreadgroup(
                    sub.group, sub.consumer, {sub.stream: ">"}, count=16, block=1000
                )
                # Crash recovery: periodically adopt messages left pending by
                # consumers (replicas) that died mid-handler.
                if not sub.ephemeral and time.monotonic() - last_claim >= self.CLAIM_EVERY_S:
                    last_claim = time.monotonic()
                    _, claimed, *_ = await self._redis.xautoclaim(
                        sub.stream, sub.group, sub.consumer,
                        min_idle_time=self._claim_idle_ms, count=16,
                    )
                    await self._process(sub, claimed)
            except asyncio.CancelledError:
                raise
            except redis_exceptions.ResponseError as e:
                if "NOGROUP" in str(e):
                    return  # group destroyed → subscription is gone
                await asyncio.sleep(1)
                continue
            except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError):
                await asyncio.sleep(1)  # broker hiccup: keep trying, the Bus stays up
                continue

            for _, messages in response or []:
                await self._process(sub, messages)

    async def _process(self, sub: _Subscription, messages) -> None:
        for msg_id, fields in messages or []:
            try:
                envelope = self._envelope_cls.model_validate_json(fields["json"])
                delivery = await self._deliver_hook(envelope, sub.callback)
                if delivery is not None:
                    # Ack AFTER the handler (and its Bus-side retries) finishes:
                    # a replica dying mid-handler leaves the message pending,
                    # and a surviving consumer reclaims it (at-least-once).
                    # shield(): a handler may unsubscribe US (poisoned-handler
                    # escalation) — cancelling this reader must not cancel the
                    # in-flight delivery that triggered it (await cycle).
                    await asyncio.shield(delivery)
            except Exception as e:
                # Corrupt/foreign message: never let it kill the reader.
                print(f"[RedisStreamsDriver] ⚠️ Undeliverable message {msg_id} on {sub.stream}: {e}")
            await self._redis.xack(sub.stream, sub.group, msg_id)

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
        if sub.task is not None:
            sub.task.cancel()
            try:
                await sub.task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            if sub.ephemeral:
                await self._redis.xgroup_destroy(sub.stream, sub.group)
            else:
                await self._redis.xgroup_delconsumer(sub.stream, sub.group, sub.consumer)
        except redis_exceptions.RedisError:
            pass  # best-effort cleanup; leftover groups are harmless

    # ─── OBSERVABILITY ────────────────────────────────────

    def get_status(self, name_resolver: Callable) -> dict:
        status: dict = {}
        for sub in self._subs:
            status.setdefault(sub.event, []).append(name_resolver(sub.callback))
        return status

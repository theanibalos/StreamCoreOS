"""
Kafka Driver — Distributed transport for the Event Bus
======================================================

A third production EventBusDriver, sibling of the Redis Streams and RabbitMQ
drivers. Pure transport: retries, DLQ, RPC, tracing and auto-unsubscribe stay
in the Bus, exactly as the replacement standard in
`tools/event_bus/event_bus_tool.py` prescribes. The Bus and every plugin are
unaffected — same EventEnvelope, same API, same semantics.

ACTIVATION (the swap — the Bus and plugins are NOT touched):
─────────────────────────────────────────────────────────────────
    1. uv add "aiokafka>=0.14"   (already in pyproject if this file shipped)
    2. Start a broker (uncomment the kafka service in
       dev_infra/docker-compose.yml).
    3. Move this file into tools/event_bus/ and set EVENT_BUS_DRIVER=kafka.
       Driver discovery is generic (same swap standard as the db tool: file
       placement IS the installation — no Bus edit, no branch to add).
       Explicit injection also works:
           from extras.available_tools.kafka.kafka_driver import KafkaDriver
           EventBusTool(driver=KafkaDriver())

With N replicas pointing at the same cluster, events published by one instance
reach subscribers in all instances — and the Bus's auto-derived `group=`
subscriptions become real Kafka consumer groups ACROSS replicas (the Elastic
Monolith scaling pattern).

TRANSPORT MAPPING:
─────────────────────────────────────────────────────────────────
    event "user.created"  → topic "bus.user.created" (prefix configurable;
        names are sanitized, hashed only if sanitization changed them).
        Topics are created on demand (KAFKA_BUS_PARTITIONS partitions).
    "_reply.*" events      → the shared topic "bus.__replies__" (an RPC reply
        channel is ephemeral — one Kafka topic per request would litter the
        cluster). The driver filters by exact event name before delivering.
        ("__replies__" and "__delayed__" are reserved event names.)
    subscribe(group="g")   → Kafka consumer group "g": the broker delivers
        each message to exactly one member of the group across the WHOLE
        fleet. The Bus auto-derives a stable group from the callback identity,
        so this is the normal path for every business subscription.
        NOTE: group parallelism is capped by KAFKA_BUS_PARTITIONS.
    subscribe(group=None)  → standalone consumer (no group), assigned all
        partitions, positioned at the log end (broadcast: each subscriber sees
        every NEW message — no replay, same "$" semantics as Redis Streams).
        Only broadcast subscriptions reach the driver without a group:
        RPC replies, broadcast=True.
    key                    → partition key: strict ordering PER KEY. Unkeyed
        events are round-robined across partitions, so cross-event ordering
        for a group consumer is NOT total — publish with key= when order
        matters (this is the Kafka contract the Bus documents).
    delay                  → NATIVE (capability claim, Issue 30): delayed
        envelopes are parked in the topic "bus.__delayed__" (a reserved
        event name, keyed by delay value) and promoted when due by a
        fleet-wide scheduler consumer group that every replica runs — the
        delay is KAFKA-persisted and survives a publisher crash. The
        scheduler holds each partition's head until due WITHOUT committing
        (paused polls keep the group session alive for arbitrarily long
        delays); a crash mid-hold redelivers to another replica. Caveat:
        messages behind a longer delay on the same __delayed__ partition
        wait for it (keying by delay value groups equal delays together,
        which makes head-of-line order = due order per delay class).
    priority               → accepted but a no-op (Kafka has no priority)
    ttl                    → enforced Bus-side at delivery (age check)

CONFIGURATION (env vars, read in __init__, zero I/O):
─────────────────────────────────────────────────────────────────
    KAFKA_BOOTSTRAP_SERVERS   default "localhost:9092" (comma-separated)
    KAFKA_CONNECT_TIMEOUT     default "5" (seconds)
    KAFKA_BUS_TOPIC_PREFIX    default "bus."
    KAFKA_BUS_PARTITIONS      default "6" (partitions for auto-created topics;
        also the max number of replicas concurrently consuming one group)
    KAFKA_BUS_REPLICATION     default "1" (raise on multi-broker clusters)
    KAFKA_MAX_POLL_INTERVAL_MS default "300000" — a consumer that goes longer
        than this between polls (i.e. a handler + Bus retries/backoff slower
        than this) is evicted from the group and its message REDELIVERED to
        another replica: it runs TWICE. RAISE it above the worst-case handler
        duration.

DELIVERY GUARANTEE: at-least-once. An offset is committed only AFTER the
handler (including Bus-side retries/DLQ) finishes — if the replica dies
mid-handler, the offset stays uncommitted and the group rebalance hands the
message to another replica. Handlers must be idempotent (already required by
the Bus contract).
"""

import os
import re
import hashlib
import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaError, TopicAlreadyExistsError, for_code

from core.base_tool import ToolUnavailableError
from tools.event_bus.event_bus_tool import EventBusDriver


class EventBusConnectionError(ToolUnavailableError):
    """Kafka cluster unreachable — ToolProxy marks the bus DEAD immediately."""
    pass


class _Subscription:
    """One reader loop: (event, callback) consuming a topic via a consumer."""

    def __init__(self, event: str, topic: str, callback: Callable,
                 ephemeral: bool):
        self.event = event
        self.topic = topic
        self.callback = callback
        self.ephemeral = ephemeral  # broadcast: standalone consumer, no group
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.task: Optional[asyncio.Task] = None
        # Set once fetch positions are pinned: a message published after
        # subscribe() returns is GUARANTEED to be at or past the position.
        self.ready = asyncio.Event()


class KafkaDriver(EventBusDriver):
    REPLIES = "__replies__"
    DELAYED = "__delayed__"
    # How long subscribe() waits for the group join + position pinning.
    READY_TIMEOUT_S = 30.0

    capabilities = {"delay": "native", "retries": "in_bus", "dlq": "in_bus"}

    def __init__(self) -> None:
        self._servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
        self._connect_timeout: float = float(os.getenv("KAFKA_CONNECT_TIMEOUT", "5"))
        self._prefix: str = os.getenv("KAFKA_BUS_TOPIC_PREFIX", "bus.")
        self._partitions: int = int(os.getenv("KAFKA_BUS_PARTITIONS", "6"))
        self._replication: int = int(os.getenv("KAFKA_BUS_REPLICATION", "1"))
        self._max_poll_interval_ms: int = int(os.getenv("KAFKA_MAX_POLL_INTERVAL_MS", "300000"))
        self._producer: Optional[AIOKafkaProducer] = None
        self._admin: Optional[AIOKafkaAdminClient] = None
        self._known_topics: set[str] = set()
        self._topic_lock = asyncio.Lock()
        self._subs: list[_Subscription] = []
        self._scheduler_consumer: Optional[AIOKafkaConsumer] = None
        self._scheduler_task: Optional[asyncio.Task] = None

    # ─── LIFECYCLE ────────────────────────────────────────

    async def setup(self) -> None:
        print(f"[System] KafkaDriver: Connecting to {self._servers}...")
        try:
            self._admin = AIOKafkaAdminClient(
                bootstrap_servers=self._servers,
                request_timeout_ms=int(self._connect_timeout * 1000),
            )
            await self._admin.start()
            self._producer = AIOKafkaProducer(
                bootstrap_servers=self._servers,
                enable_idempotence=True,  # implies acks="all": durable publishes
            )
            await self._producer.start()
        except (KafkaError, OSError, asyncio.TimeoutError) as e:
            await self._close_clients()
            raise EventBusConnectionError(
                f"Cannot connect to Kafka cluster at {self._servers}: {e}"
            ) from e
        # Every replica runs the delay scheduler: delayed envelopes left by a
        # dead publisher still fire as long as ANY replica is alive (the
        # native-delay claim). One fleet-wide group → no double promotion.
        await self._start_delay_scheduler()
        print("[System] KafkaDriver: Distributed transport ready.")

    async def shutdown(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
            self._scheduler_task = None
        if self._scheduler_consumer is not None:
            try:
                await self._scheduler_consumer.stop()
            except (KafkaError, OSError):
                pass
            self._scheduler_consumer = None
        for sub in list(self._subs):
            await self._stop_subscription(sub)
        self._subs.clear()
        await self._close_clients()

    async def _close_clients(self) -> None:
        if self._producer is not None:
            try:
                await self._producer.stop()
            except (KafkaError, OSError):
                pass
            self._producer = None
        if self._admin is not None:
            try:
                await self._admin.close()
            except (KafkaError, OSError):
                pass
            self._admin = None

    # ─── NAMING (pure: every replica derives the same names) ──

    def _topic_for_event(self, event: str) -> str:
        if event.startswith("_reply."):
            return f"{self._prefix}{self.REPLIES}"
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", event)
        if safe != event or len(self._prefix) + len(safe) > 249:
            # Sanitization may collide distinct events ("a/b" vs "a_b"):
            # a short hash of the ORIGINAL name keeps the mapping injective.
            digest = hashlib.sha1(event.encode()).hexdigest()[:12]
            safe = f"{safe[: 249 - len(self._prefix) - len(digest) - 1]}.{digest}"
        return f"{self._prefix}{safe}"

    def _group_id(self, group: str) -> str:
        """Bus consumer groups (callback identities) → valid Kafka group ids.

        Same recipe as the RabbitMQ queue names: sanitized readable prefix
        plus a short hash of the full group — valid AND collision-free."""
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", group)
        digest = hashlib.sha1(group.encode()).hexdigest()[:12]
        prefix = f"{self._prefix}"
        return f"{prefix}{safe[: 249 - len(prefix) - len(digest) - 1]}.{digest}"

    async def _ensure_topic(self, topic: str) -> None:
        if topic in self._known_topics:
            return
        async with self._topic_lock:
            if topic in self._known_topics:
                return
            response = await self._admin.create_topics([NewTopic(
                name=topic,
                num_partitions=self._partitions,
                replication_factor=self._replication,
            )])
            for _topic, code, *_ in response.topic_errors:
                if code not in (0, TopicAlreadyExistsError.errno):
                    raise for_code(code)(f"Cannot create topic {_topic}")
            self._known_topics.add(topic)

    # ─── TRANSPORT: publish ───────────────────────────────

    async def publish(self, envelope) -> None:
        value = envelope.model_dump_json().encode()
        if envelope.delay and envelope.delay > 0:
            # Native delay: park it in Kafka NOW (crash-safe); the fleet's
            # scheduler group promotes it when due. Keyed by delay value so
            # equal delays share partitions (head-of-line order = due order).
            delayed = f"{self._prefix}{self.DELAYED}"
            try:
                await self._ensure_topic(delayed)
                await self._producer.send_and_wait(
                    delayed, value, key=str(envelope.delay).encode())
            except (KafkaError, OSError) as e:
                raise EventBusConnectionError(f"Kafka cluster unreachable: {e}") from e
            return
        key = envelope.key.encode() if envelope.key else None
        try:
            await self._ensure_topic(self._topic_for_event(envelope.event))
            await self._producer.send_and_wait(
                self._topic_for_event(envelope.event), value, key=key)
        except (KafkaError, OSError) as e:
            raise EventBusConnectionError(f"Kafka cluster unreachable: {e}") from e

    # ─── NATIVE DELAY: fleet scheduler ────────────────────

    async def _start_delay_scheduler(self) -> None:
        delayed = f"{self._prefix}{self.DELAYED}"
        await self._ensure_topic(delayed)
        self._scheduler_consumer = AIOKafkaConsumer(
            delayed,
            bootstrap_servers=self._servers,
            group_id=f"{self._prefix}{self.DELAYED}.scheduler",
            enable_auto_commit=False,       # commit only AFTER promotion
            auto_offset_reset="earliest",   # a delayed envelope must never be skipped
            max_poll_interval_ms=self._max_poll_interval_ms,
        )
        await self._scheduler_consumer.start()
        self._scheduler_task = asyncio.create_task(self._delay_scheduler())

    async def _delay_scheduler(self) -> None:
        consumer = self._scheduler_consumer
        while True:
            try:
                batches = await consumer.getmany(timeout_ms=1000, max_records=16)
                for tp, messages in (batches or {}).items():
                    for msg in messages:
                        await self._promote_when_due(consumer, tp, msg)
            except asyncio.CancelledError:
                raise
            except (KafkaError, OSError):
                await asyncio.sleep(1)  # broker hiccup: envelopes stay parked
                # The in-session fetch position advanced past uncommitted
                # messages — rewind to the last commit so none stay stranded.
                try:
                    for tp in consumer.assignment():
                        committed = await consumer.committed(tp)
                        if committed is not None:
                            consumer.seek(tp, committed)
                        else:
                            await consumer.seek_to_beginning(tp)
                except (KafkaError, OSError):
                    pass  # rebalance in flight resets positions anyway

    async def _promote_when_due(self, consumer, tp, msg) -> None:
        try:
            envelope = self._envelope_cls.model_validate_json(msg.value)
            due = envelope.timestamp.timestamp() + (envelope.delay or 0)
            while True:
                wait_s = due - datetime.now(timezone.utc).timestamp()
                if wait_s <= 0:
                    break
                # Hold WITHOUT committing: pause fetching and keep polling —
                # the empty polls keep the group session alive for arbitrarily
                # long delays, and a crash here redelivers (nothing committed).
                consumer.pause(*consumer.assignment())
                try:
                    await consumer.getmany(timeout_ms=min(int(wait_s * 1000) + 1, 1000))
                finally:
                    consumer.resume(*consumer.assignment())
            # Due: forward to the real topic (this is the actual "publish").
            key = envelope.key.encode() if envelope.key else None
            await self._ensure_topic(self._topic_for_event(envelope.event))
            await self._producer.send_and_wait(
                self._topic_for_event(envelope.event), msg.value, key=key)
        except (KafkaError, OSError):
            raise  # let the scheduler loop back off and re-poll (uncommitted)
        except Exception as e:
            # Corrupt/foreign message: skip it (committed below), never wedge.
            print(f"[KafkaDriver] ⚠️ Undeliverable delayed message: {e}")
        try:
            await consumer.commit({tp: msg.offset + 1})
        except (KafkaError, OSError):
            pass  # rebalance in flight → re-promotion (at-least-once)

    # ─── TRANSPORT: subscribe / readers ───────────────────

    async def subscribe(self, event_name: str, group: Optional[str], callback: Callable):
        topic = self._topic_for_event(event_name)
        ephemeral = group is None
        try:
            await self._ensure_topic(topic)
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=self._servers,
                # None → standalone consumer: all partitions, no rebalancing —
                # every broadcast subscriber sees every message.
                group_id=None if ephemeral else self._group_id(group),
                enable_auto_commit=False,     # commits happen AFTER the handler
                auto_offset_reset="latest",   # no replay on subscribe ("$")
                max_poll_interval_ms=self._max_poll_interval_ms,
            )
            await consumer.start()
        except (KafkaError, OSError, asyncio.TimeoutError) as e:
            raise EventBusConnectionError(
                f"Cannot subscribe to Kafka topic {topic}: {e}") from e

        sub = _Subscription(event_name, topic, callback, ephemeral)
        sub.consumer = consumer
        sub.task = asyncio.create_task(self._reader(sub))
        self._subs.append(sub)
        # Do not return until fetch positions are pinned: the RPC pattern
        # (subscribe reply → publish request) must never miss the reply.
        await asyncio.wait_for(sub.ready.wait(), timeout=self.READY_TIMEOUT_S)

    async def _reader(self, sub: _Subscription) -> None:
        consumer = sub.consumer
        while True:
            try:
                if not sub.ready.is_set() and consumer.assignment():
                    # Assignment arrived (group joined / standalone metadata):
                    # force the position lookup NOW (committed offset, or log
                    # end for a fresh group) so subscribe() can safely return.
                    for tp in consumer.assignment():
                        await consumer.position(tp)
                    sub.ready.set()
                batches = await consumer.getmany(
                    timeout_ms=100 if not sub.ready.is_set() else 1000,
                    max_records=16,
                )
            except asyncio.CancelledError:
                raise
            except (KafkaError, OSError):
                await asyncio.sleep(1)  # broker hiccup: keep trying, the Bus stays up
                continue
            await self._process(sub, batches)

    async def _process(self, sub: _Subscription, batches) -> None:
        for tp, messages in (batches or {}).items():
            for msg in messages:
                try:
                    envelope = self._envelope_cls.model_validate_json(msg.value)
                    # The shared __replies__ topic carries foreign events:
                    # deliver only what this subscription asked for.
                    if envelope.event == sub.event:
                        delivery = await self._deliver_hook(envelope, sub.callback)
                        if delivery is not None:
                            # Commit AFTER the handler (and its Bus-side
                            # retries) finishes: a replica dying mid-handler
                            # leaves the offset uncommitted and the group
                            # redelivers (at-least-once).
                            # shield(): a handler may unsubscribe US
                            # (poisoned-handler escalation) — cancelling this
                            # reader must not cancel the in-flight delivery
                            # that triggered it (await cycle).
                            await asyncio.shield(delivery)
                except Exception as e:
                    # Corrupt/foreign message: never let it kill the reader.
                    print(f"[KafkaDriver] ⚠️ Undeliverable message on {sub.topic}: {e}")
                if not sub.ephemeral:
                    try:
                        await sub.consumer.commit({tp: msg.offset + 1})
                    except (KafkaError, OSError):
                        pass  # rebalance in flight → redelivery (at-least-once)

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
            # Durable groups survive (committed offsets stay in the broker,
            # like Redis XGROUP DELCONSUMER): the rest of the fleet keeps
            # consuming. Standalone consumers leave nothing behind.
            if sub.consumer is not None:
                await sub.consumer.stop()
        except (KafkaError, OSError):
            pass  # best-effort cleanup; the broker reaps orphans on disconnect

    # ─── OBSERVABILITY ────────────────────────────────────

    def get_status(self, name_resolver: Callable) -> dict:
        status: dict = {}
        for sub in self._subs:
            status.setdefault(sub.event, []).append(name_resolver(sub.callback))
        return status

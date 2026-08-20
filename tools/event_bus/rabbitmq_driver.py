"""
RabbitMQ Driver — Distributed transport for the Event Bus
=========================================================

A second production EventBusDriver, sibling of the Redis Streams driver.
Pure transport: retries, DLQ, RPC, tracing and auto-unsubscribe stay in the
Bus, exactly as the replacement standard in
`tools/event_bus/event_bus_tool.py` prescribes. The Bus and every plugin are
unaffected — same EventEnvelope, same API, same semantics.

ACTIVATION (the swap — the Bus and plugins are NOT touched):
─────────────────────────────────────────────────────────────────
    1. uv add "aio-pika>=9.4"   (already in pyproject if this file shipped)
    2. Start a broker (uncomment the rabbitmq service in
       dev_infra/docker-compose.yml).
    3. Move this file into tools/event_bus/ and set EVENT_BUS_DRIVER=rabbitmq.
       Driver discovery is generic (same swap standard as the db tool: file
       placement IS the installation — no Bus edit, no branch to add).
       Explicit injection also works:
           from extras.available_tools.rabbitmq.rabbitmq_driver import RabbitMQDriver
           EventBusTool(driver=RabbitMQDriver())

With N replicas pointing at the same broker, events published by one instance
reach subscribers in all instances — and the Bus's auto-derived `group=`
subscriptions become real exactly-one-consumer queues ACROSS replicas (the
Elastic Monolith scaling pattern).

TRANSPORT MAPPING:
─────────────────────────────────────────────────────────────────
    one durable TOPIC exchange (RABBITMQ_BUS_EXCHANGE, default "bus")
    event "user.created"  → published with routing key "user.created"
    subscribe(group="g")  → durable queue "{exchange}.g" bound to the routing
        key. Multiple consumers on that queue are COMPETING consumers: the
        broker hands each message to exactly one of them across the WHOLE
        fleet. The Bus auto-derives a stable group from the callback identity,
        so this is the normal path for every business subscription.
    subscribe(group=None) → exclusive, auto-delete, server-named queue
        (broadcast: each subscriber gets its own copy; the queue dies on
        unsubscribe). Only broadcast subscriptions reach the driver without a
        group: RPC replies, broadcast=True.
    priority              → message priority (queues declared x-max-priority 10)
    delay                 → NATIVE (capability claim, Issue 30) via the
        TTL + dead-letter pattern, stock broker only: a delayed publish goes
        to the wait queue "{exchange}.delay.{n}" (x-message-ttl = n seconds,
        x-dead-letter-exchange = the bus exchange, no consumers). On expiry
        the broker republishes it to the bus exchange with its original
        routing key — the delay is BROKER-persisted and survives a publisher
        crash. One wait queue per distinct delay value (all messages in a
        queue share the TTL, so head-of-queue expiry is strictly FIFO).
    key                   → accepted but a no-op (a queue is already ordered)
    ttl                   → enforced Bus-side at delivery (age check), NOT via
        broker message expiration: the Bus must still RECEIVE the message to
        record the "ttl_expired" trace node.

CONFIGURATION (env vars, read in __init__, zero I/O):
─────────────────────────────────────────────────────────────────
    RABBITMQ_HOST           default "localhost"
    RABBITMQ_PORT           default "5672"
    RABBITMQ_USER           default "guest"
    RABBITMQ_PASSWORD       default "guest"
    RABBITMQ_VHOST          default "/"
    RABBITMQ_CONNECT_TIMEOUT default "5" (seconds)
    RABBITMQ_BUS_EXCHANGE   default "bus"
    RABBITMQ_PREFETCH       default "16" (unacked messages in flight per queue)

DELIVERY GUARANTEE: at-least-once. A message is acked only AFTER the handler
(including the Bus-side retries/DLQ) finishes — if the replica dies mid-handler
the channel drops, the message stays unacked and the broker redelivers it to
another consumer of the same queue. Handlers must be idempotent (already
required by the Bus contract).
"""

import os
import re
import hashlib
import asyncio
import aio_pika
from aio_pika.abc import AbstractRobustConnection, AbstractRobustChannel, AbstractExchange
from typing import Callable, Optional
from core.base_tool import ToolUnavailableError
from tools.event_bus.event_bus_tool import EventBusDriver


class EventBusConnectionError(ToolUnavailableError):
    """RabbitMQ broker unreachable — ToolProxy marks the bus DEAD immediately."""
    pass


class _Subscription:
    """One consumer: (event, callback) draining a queue bound to the exchange."""

    def __init__(self, event: str, callback: Callable, ephemeral: bool):
        self.event = event
        self.callback = callback
        self.ephemeral = ephemeral  # broadcast queues are exclusive/auto-delete
        self.channel: Optional[AbstractRobustChannel] = None
        self.queue = None
        self.consumer_tag: Optional[str] = None


class RabbitMQDriver(EventBusDriver):
    MAX_PRIORITY = 10

    capabilities = {"delay": "native", "retries": "in_bus", "dlq": "in_bus"}

    def __init__(self) -> None:
        self._host: str = os.getenv("RABBITMQ_HOST", "localhost")
        self._port: int = int(os.getenv("RABBITMQ_PORT", "5672"))
        self._user: str = os.getenv("RABBITMQ_USER", "guest")
        self._password: str = os.getenv("RABBITMQ_PASSWORD", "guest")
        self._vhost: str = os.getenv("RABBITMQ_VHOST", "/")
        self._connect_timeout: float = float(os.getenv("RABBITMQ_CONNECT_TIMEOUT", "5"))
        self._exchange_name: str = os.getenv("RABBITMQ_BUS_EXCHANGE", "bus")
        self._prefetch: int = int(os.getenv("RABBITMQ_PREFETCH", "16"))
        self._connection: Optional[AbstractRobustConnection] = None
        self._pub_channel: Optional[AbstractRobustChannel] = None
        self._pub_exchange: Optional[AbstractExchange] = None
        self._pub_lock = asyncio.Lock()
        self._subs: list[_Subscription] = []

    # ─── LIFECYCLE ────────────────────────────────────────

    async def setup(self) -> None:
        print(f"[System] RabbitMQDriver: Connecting to {self._host}:{self._port}{self._vhost}...")
        try:
            self._connection = await aio_pika.connect_robust(
                host=self._host, port=self._port, login=self._user,
                password=self._password, virtualhost=self._vhost,
                timeout=self._connect_timeout,
            )
            self._pub_channel = await self._connection.channel(publisher_confirms=True)
            self._pub_exchange = await self._declare_exchange(self._pub_channel)
        except (aio_pika.exceptions.AMQPError, OSError, asyncio.TimeoutError) as e:
            raise EventBusConnectionError(
                f"Cannot connect to RabbitMQ broker at {self._host}:{self._port}{self._vhost}: {e}"
            ) from e
        print("[System] RabbitMQDriver: Distributed transport ready.")

    async def shutdown(self) -> None:
        for sub in list(self._subs):
            await self._stop_subscription(sub)
        self._subs.clear()
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _declare_exchange(self, channel) -> AbstractExchange:
        return await channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )

    # ─── TRANSPORT: publish ───────────────────────────────

    async def _delay_exchange(self, delay_s: int) -> AbstractExchange:
        """Wait-queue infra for one delay value.

        A topic exchange + TTL'd queue pair named "{exchange}.delay.{n}".
        Publishing THROUGH the exchange (binding "#") preserves the event's
        routing key, so on expiry the dead-letter republish routes normally.

        Deliberately re-declared on EVERY delayed publish (no cache):
        declaration counts as "usage" for x-expires, so the broker reaps a
        wait queue only delay+1h after the LAST publish of that value — by
        then every parked message has long expired and been forwarded. This
        keeps arbitrary delay values from accumulating queues forever."""
        name = f"{self._exchange_name}.delay.{delay_s}"
        exchange = await self._pub_channel.declare_exchange(
            name, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await self._pub_channel.declare_queue(
            name, durable=True,
            arguments={
                "x-message-ttl": delay_s * 1000,
                "x-dead-letter-exchange": self._exchange_name,
                "x-expires": delay_s * 1000 + 3_600_000,
            },
        )
        await queue.bind(name, routing_key="#")
        return exchange

    async def publish(self, envelope) -> None:
        priority = None
        if envelope.priority is not None:
            priority = max(0, min(self.MAX_PRIORITY, int(envelope.priority)))

        message = aio_pika.Message(
            body=envelope.model_dump_json().encode(),
            content_type="application/json",
            priority=priority,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        try:
            # A robust channel survives reconnects but is not safe for
            # concurrent publishers — the Bus fires publishes as parallel tasks.
            async with self._pub_lock:
                if envelope.delay and envelope.delay > 0:
                    # Native delay: park it broker-side NOW (crash-safe); the
                    # TTL expiry dead-letters it into the bus exchange.
                    exchange = await self._delay_exchange(int(envelope.delay))
                else:
                    exchange = self._pub_exchange
                await exchange.publish(message, routing_key=envelope.event)
        except (aio_pika.exceptions.AMQPError, OSError) as e:
            raise EventBusConnectionError(f"RabbitMQ broker unreachable: {e}") from e

    # ─── TRANSPORT: subscribe / consumers ─────────────────

    async def subscribe(self, event_name: str, group: Optional[str], callback: Callable):
        ephemeral = group is None

        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=self._prefetch)
        await self._declare_exchange(channel)

        if ephemeral:
            # Broadcast: a private queue, gone the moment we stop consuming.
            queue = await channel.declare_queue(
                exclusive=True, auto_delete=True,
                arguments={"x-max-priority": self.MAX_PRIORITY},
            )
        else:
            # Durable competing-consumer queue shared by the whole group/fleet.
            queue = await channel.declare_queue(
                self._queue_name(group), durable=True,
                arguments={"x-max-priority": self.MAX_PRIORITY},
            )
        await queue.bind(self._exchange_name, routing_key=event_name)

        sub = _Subscription(event_name, callback, ephemeral)
        sub.channel = channel
        sub.queue = queue

        async def _consumer(message):
            await self._on_message(sub, message)

        sub.consumer_tag = await queue.consume(_consumer)
        self._subs.append(sub)

    def _queue_name(self, group: str) -> str:
        """Derive a valid, deterministic queue name from a Bus consumer group.

        The Bus derives groups from callback identity (e.g.
        "users.WelcomeServicePlugin.on_user_created"), which can contain
        characters RabbitMQ forbids in queue names (and may exceed 256 bytes).
        Every replica derives the SAME group, so the mapping must be pure: a
        sanitized, readable prefix plus a short hash of the full group keeps it
        valid AND collision-free across the fleet."""
        safe = re.sub(r"[^a-zA-Z0-9_.:-]", "_", group)
        digest = hashlib.sha1(group.encode()).hexdigest()[:12]
        prefix = f"{self._exchange_name}."
        return f"{prefix}{safe[: 256 - len(prefix) - len(digest) - 1]}.{digest}"

    async def _on_message(self, sub: _Subscription, message) -> None:
        try:
            envelope = self._envelope_cls.model_validate_json(message.body.decode())
            delivery = await self._deliver_hook(envelope, sub.callback)
            if delivery is not None:
                # Ack AFTER the handler (and its Bus-side retries) finishes: a
                # replica dying mid-handler leaves the message unacked and the
                # broker redelivers it (at-least-once).
                # shield(): a handler may unsubscribe US (poisoned-handler
                # escalation) — cancelling this consumer must not cancel the
                # in-flight delivery that triggered it (await cycle).
                await asyncio.shield(delivery)
        except Exception as e:
            # Corrupt/foreign message: never let it wedge the consumer.
            print(f"[RabbitMQDriver] ⚠️ Undeliverable message on {sub.event}: {e}")
        try:
            await message.ack()
        except aio_pika.exceptions.AMQPError:
            pass  # channel already gone (e.g. we were just unsubscribed)

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
        try:
            if sub.queue is not None and sub.consumer_tag is not None:
                await sub.queue.cancel(sub.consumer_tag)
            # Durable group queues survive (like Redis XGROUP DELCONSUMER): the
            # rest of the fleet keeps consuming. Broadcast queues are
            # exclusive/auto-delete, so closing the channel reclaims them.
            if sub.channel is not None:
                await sub.channel.close()
        except aio_pika.exceptions.AMQPError:
            pass  # best-effort cleanup; the broker reaps orphans on disconnect

    # ─── OBSERVABILITY ────────────────────────────────────

    def get_status(self, name_resolver: Callable) -> dict:
        status: dict = {}
        for sub in self._subs:
            status.setdefault(sub.event, []).append(name_resolver(sub.callback))
        return status

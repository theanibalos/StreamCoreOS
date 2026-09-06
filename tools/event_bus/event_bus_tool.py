"""
Enterprise Event Bus — Universal Elastic Monolith Core
======================================================
Definitive Version: Pydantic-native traceability and industrial drivers.

PUBLIC CONTRACT (what plugins use):
────────────────────────────────────────────────────────────────────────────────
    await bus.publish("user.created", {"id": 1}, key=None, priority=None,
                      delay=None, ttl=None, correlation_id=None)
    await bus.subscribe("user.created", self.on_event, group=None, retries=0,
                        backoff=0.5, broadcast=False)
    reply = await bus.request("user.lookup", {"id": 1}, timeout=5)
    await bus.unsubscribe("user.created", self.on_event)

    Subscribers ALWAYS receive an EventEnvelope: async def on_event(self, event: EventEnvelope)

CONSUMER IDENTITY (how replicas are recognized — Elastic Monolith core rule):
    group=None (default)  → the Bus derives a STABLE group from the callback
        identity (e.g. "WelcomeServicePlugin.on_user_created"). Every replica
        runs the same code, derives the same group, and the broker delivers
        each event to exactly ONE replica. Distinct plugins derive distinct
        groups, so each logical consumer still gets its own copy.
    group="workers"       → explicit worker pool (exactly-one across the pool).
    broadcast=True        → EVERY instance receives a copy. Only for
        instance-local concerns (cache invalidation, local metrics).
        RPC reply subscriptions are always broadcast.
    (There is deliberately NO wildcard subscription: system-wide observation
    is add_listener()'s job in-process, and the broker's own tooling when
    distributed — an audit consumer reads the topics/streams directly.)

UNIVERSAL HINTS (kwargs):
- key: String. The ordering unit: same-key publishes reach the transport
  in call order, on every driver. Across keys nothing is promised (Kafka
  orders per partition, SQS FIFO per MessageGroupId — same shape).
- priority: Integer (1-10). Importance (RabbitMQ).
- delay: Integer (seconds). Delivery schedule.
- ttl: Float (seconds). Message expiration (Broker-side).
- correlation_id: String. RPC tracking.

REPLACEMENT STANDARD (swap the transport, not the tool):
────────────────────────────────────────────────────────────────────────────────
Unlike other tools, you do NOT rewrite EventBusTool to go distributed.
Retries, backoff, DLQ, RPC, tracing and auto-unsubscribe are broker-agnostic
and live in the Bus. Only TRANSPORT is delegated, via the EventBusDriver
interface below (reference implementation: InProcessDriver).

To swap to Kafka/RabbitMQ/Redis Streams:
    1. Implement EventBusDriver (publish / subscribe / unsubscribe /
       unsubscribe_all / get_status / setup / shutdown).
    2. publish() is pure fire-and-forget: serialize the EventEnvelope
       (envelope.model_dump_json()) and hand it to the broker. Map hints:
       key → partition key (Kafka), priority → message priority (RabbitMQ),
       ttl → broker-side expiration. For delay, declare a capability claim
       (Issue 30): `capabilities = {"delay": "native", ...}` means YOUR
       publish() persists the delayed envelope broker-side (crash-safe);
       leave the default "in_bus" and the Bus sleeps the delay for you
       (publisher-memory only — a crash during the wait loses the event).
    3. On message arrival, deserialize with self._envelope_cls (injected by
       the Bus via bind(), so a Bus constructed with a custom envelope class
       still validates against its own) and call
       self._deliver_hook(envelope, callback) — the Bus takes
       over from there (retries, DLQ, tracing all still work).
    4. Install it the same way tools are swapped — file placement, no code
       edits: drop the driver in as tools/event_bus/{name}_driver.py and set
       EVENT_BUS_DRIVER={name}. Discovery is generic (ready-made drivers ship
       in extras/available_tools/, e.g. rabbitmq). Explicit injection also
       works: EventBusTool(driver=KafkaDriver()).
    5. It MUST pass the parity suite: tests/tools/event_bus/test_event_bus_broker_parity.py.

Plugins are unaffected: same envelope, same API, same semantics.
"""

import collections
import importlib
import uuid
import asyncio
import inspect
import os
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, List, Tuple, Set
from microcoreos import BaseTool
from microcoreos import current_event_id_var, current_identity_var
from tools.event_bus.envelope import EventEnvelope, TraceNode, TraceRecord, SubOptions  # noqa: F401 — re-export
from tools.event_bus.drivers import EventBusDriver, InProcessDriver

# EventEnvelope, TraceNode, TraceRecord, SubOptions live in envelope.py and
# EventBusDriver / InProcessDriver live in drivers.py — re-exported above so
# `from tools.event_bus.event_bus_tool import EventBusTool, EventEnvelope,
# EventBusDriver, InProcessDriver` keeps working for every existing caller
# (including the sqlite/redis/kafka/rabbitmq drivers, which import
# EventBusDriver from THIS module by that exact path).


class EventBusTool(BaseTool):
    _MAX_CONSECUTIVE_FAILURES = 5
    SUBSCRIBER_DROPPED_EVENT = "system.subscriber.dropped"

    def __init__(self, driver: Optional[EventBusDriver] = None):
        self._driver = driver or self._driver_from_env()
        self._trace_log: collections.deque = collections.deque(maxlen=500)
        self._listeners: list = []
        self._failure_listeners: list = []
        self._consecutive_failures: dict[tuple[str, str], int] = {}
        self._pending_tasks: Set[asyncio.Task] = set()
        # Ordering unit → the last publish handed to the transport for it.
        # See publish(): this is what keeps same-key publishes in call order.
        self._publish_chain: Dict[str, asyncio.Task] = {}
        self._sub_options: Dict[Tuple[str, Callable], SubOptions] = {}
        # Chaos/ops pause (Issue 34): owner identities ("domain.Class", or a
        # bare domain prefix) whose deliveries are held. Deliberately NOT
        # public API (the contract is frozen — Issue 36): mutated only by the
        # chaos extras plugin via its sanctioned raw-tool introspection.
        self._paused_owners: Set[str] = set()

        # Bind the delivery hook (and OUR envelope class — see EventBusDriver.bind)
        self._driver.bind(self._deliver, EventEnvelope)

    @staticmethod
    def _driver_from_env() -> EventBusDriver:
        """Transport selection without touching code: EVENT_BUS_DRIVER env var.

        Same swap standard as the db tool, applied to transports: any
        tools/event_bus/{name}_driver.py defining an EventBusDriver subclass
        is installed by dropping the file in — EVENT_BUS_DRIVER={name} selects
        it. No branch to add here, ever.
        """
        name = os.getenv("EVENT_BUS_DRIVER", "in_process").strip().lower()
        if name in ("", "in_process", "inprocess", "memory"):
            return InProcessDriver()

        driver_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), f"{name}_driver.py"
        )
        if not os.path.exists(driver_file):
            raise ValueError(
                f"Unknown EVENT_BUS_DRIVER '{name}': tools/event_bus/{name}_driver.py "
                f"not found. Installing a transport = dropping its *_driver.py file "
                f"there (ready-made drivers ship in extras/available_tools/)."
            )

        module = importlib.import_module(f"tools.event_bus.{name}_driver")
        for obj in vars(module).values():
            if (isinstance(obj, type) and obj.__module__ == module.__name__
                    and issubclass(obj, EventBusDriver) and obj is not EventBusDriver):
                return obj()
        raise ValueError(
            f"tools/event_bus/{name}_driver.py defines no EventBusDriver subclass."
        )

    @property
    def name(self) -> str: return "event_bus"

    async def setup(self) -> None:
        try:
            await self._driver.setup()
        except BaseException as setup_err:
            try:
                await self.shutdown()
            except Exception as cleanup_err:
                print(f"[EventBusTool] ⚠️  Cleanup error during failed setup teardown: {cleanup_err}")
            raise setup_err
        print(f"[System] EventBusTool: Online (Universal Driver: {self._driver.__class__.__name__}).")

    def get_interface_description(self) -> str:
        return """
        Universal Event Bus (event_bus):
        - publish(event_name, data, **kwargs): Broadcast an event.
        - subscribe(event_name, callback, group=None, retries=0, backoff=0.5, broadcast=False):
          Listen for events. group=None derives a STABLE group from the callback identity:
          replicas of the same plugin consume each event exactly once across the fleet,
          while distinct plugins each get their own copy. Use group="pool" for explicit
          worker pools, broadcast=True ONLY for instance-local concerns (every replica
          receives a copy — e.g. local cache invalidation).
        - request(event_name, data, timeout=5): Async RPC (returns dict).
        - unsubscribe(event_name, callback): Stop listening.
        - get_trace_history() -> List[TraceNode]: Last 500 event records.
        - get_subscribers() -> dict: Current subscriber map.
        - add_listener(callback): Sink for all events (record: dict).
        - add_failure_listener(callback): Sink for errors (record: dict).
        
        CRITICAL: Subscribing callbacks receive the event envelope as their single
        argument — read event.payload. Leave the parameter untyped (no annotation,
        no import needed): async def on_event(self, event): print(event.payload)
        
        RETRIES & IDEMPOTENCY:
        - If 'retries' > 0, the handler will be re-executed on failure with exponential backoff.
        - Ensure handlers are idempotent as they may run multiple times.

        DEAD-LETTER QUEUE (DLQ):
        - Final failures are published to '_dlq.<original_event>'.
        - Payload includes 'original' envelope, 'subscriber', 'error', and 'attempts'.
        - Loop protection: '_dlq.*' and '_reply.*' events are never dead-lettered.
        - Toggle via EVENT_BUS_DLQ_ENABLED (default: true).

        UNIVERSAL CAPABILITIES (kwargs):
        - key: String. Strict ordering PER KEY. Without a key, do NOT assume
          cross-event ordering: it varies by transport (total in-process,
          partition-dependent on Kafka).
        - priority: Integer (1-10). Importance (RabbitMQ).
        - delay: Integer (seconds). Delivery schedule. Crash-safe only when
          the active transport claims delay=native (see ACTIVE TRANSPORT).
        - ttl: Float (seconds). Message expiration hint. Counted from PUBLISH
          time and therefore INCLUDES any delay (delay=60 + ttl=30 expires
          before it can ever be delivered).
        - correlation_id: String. Cross-reference for RPC.

        RESILIENCE:
        - A subscriber that reaches 5 consecutive FINAL failures for a specific event is auto-unsubscribed.
        - Each auto-unsubscribe publishes 'system.subscriber.dropped'
          (payload: event, subscriber, error, consecutive_failures) so the drop
          is observable — subscribe to it for alerting/monitoring.

        ACTIVE TRANSPORT: {driver} — capability claims: {caps}
        ("native" = the broker implements it, crash-safe; "in_bus" = software
        fallback in this process' memory).
        """.format(driver=self._driver.__class__.__name__,
                   caps=self._driver.capabilities)

    async def subscribe(self, event_name: str, callback: Callable, group: Optional[str] = None,
                        retries: int = 0, backoff: float = 0.5, broadcast: bool = False):
        self._sub_options[(event_name, callback)] = SubOptions(retries=retries, backoff=backoff)
        if group is None and not broadcast and not event_name.startswith("_reply."):
            # Stable consumer identity: every replica runs the same code and
            # derives the same group → the fleet consumes each event exactly
            # once per logical consumer. Distinct plugins → distinct groups →
            # each still receives its own copy. Within a single instance this
            # is indistinguishable from the old broadcast behavior.
            group = self._get_name(callback)
        await self._driver.subscribe(event_name, group, callback)

    async def unsubscribe(self, event_name: str, callback: Callable):
        for key in list(self._sub_options.keys()):
            if key[1] == callback:
                del self._sub_options[key]
        await self._driver.unsubscribe(event_name, callback)

    async def publish(self, event_name: str, data: dict, **kwargs):
        kwargs.pop("emitter", None)
        envelope = EventEnvelope(
            event=event_name, payload=data,
            emitter=current_identity_var.get() or "system",
            parent_id=current_event_id_var.get(),
            **kwargs
        )
        
        # 1. Record Publication (Tracing)
        # Note: In a distributed system, we don't know the subscribers yet.
        record = TraceNode(kind="published", envelope=envelope)
        self._trace_log.append(record)
        
        raw_record = {
            **envelope.model_dump(), 
            "kind": "published",
            "payload_keys": list(envelope.payload.keys()),
            "timestamp": envelope.timestamp.timestamp()
        }
        for listener in self._listeners:
            try:
                res = listener(raw_record)
                if inspect.isawaitable(res):
                    task = asyncio.create_task(res)
                    self._pending_tasks.add(task)
                    task.add_done_callback(self._pending_tasks.discard)
            except Exception: pass
        
        print(f"[EventBus] 📣 {envelope.event} [{envelope.id[:8]}]")

        # 2. Hand over to Driver — fire and forget for the CALLER, ordered
        #    for the transport.
        #
        #    Publication stays decoupled: publish() returns without waiting for
        #    the broker. What must not leak out of that is the ORDER. These
        #    hand-offs used to race each other, so the order messages reached
        #    the transport was the order threads happened to win in — and a
        #    durable queue then stored, and delivered, that scrambled order
        #    faithfully. Three publishes in a row could arrive 1, 0, 2.
        #
        #    Each publish now chains onto the previous one for its ordering
        #    unit. The chain is built HERE, synchronously on the event loop,
        #    which is the one point in this path where order is deterministic —
        #    not left to task start order or lock fairness.
        #
        #    The unit is the partition key, falling back to the event name:
        #    the guarantee every broker actually makes (Kafka orders per
        #    partition, SQS FIFO per MessageGroupId, RabbitMQ per queue).
        #    Different keys still publish in parallel; only same-key ones queue.
        unit = envelope.key or envelope.event
        previous = self._publish_chain.get(unit)
        task = asyncio.create_task(self._transport_publish_after(previous, envelope))
        self._publish_chain[unit] = task
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(lambda t, u=unit: self._release_chain(u, t))

    def _release_chain(self, unit: str, task: asyncio.Task) -> None:
        """Drop a finished chain tail so `_publish_chain` cannot grow forever.

        Only while it IS still the tail: if a later publish under the same unit
        has already chained onto this task, removing it here would let the next
        one start unordered.
        """
        if self._publish_chain.get(unit) is task:
            del self._publish_chain[unit]

    async def _transport_publish_after(self, previous, envelope: EventEnvelope) -> None:
        if previous is not None:
            # Wait for the predecessor to reach the transport, but never inherit
            # its fate: one publish failing must not silently drop every later
            # message under the same key.
            try:
                await previous
            except Exception:
                pass
        await self._transport_publish(envelope)

    async def _transport_publish(self, envelope: EventEnvelope) -> None:
        """Universal software fallbacks (Issue 30) + hand-off to the driver.

        The Bus sleeps the delay ONLY when the driver does not claim it
        natively — a native delay is broker-persisted and survives a
        publisher crash, so the driver must receive the envelope NOW.
        """
        if (envelope.delay and envelope.delay > 0
                and self._driver.capabilities.get("delay") != "native"):
            await asyncio.sleep(envelope.delay)
        await self._driver.publish(envelope)

    async def request(self, event_name: str, data: dict, timeout: float = 5):
        correlation_id = str(uuid.uuid4())
        reply_to = f"_reply.{event_name}.{uuid.uuid4().hex[:8]}"
        future = asyncio.get_running_loop().create_future()
        
        async def _collector(env: EventEnvelope):
            if env.correlation_id == correlation_id and not future.done():
                future.set_result(env.payload)
        
        await self.subscribe(reply_to, _collector)
        try:
            await self.publish(event_name, data, reply_to=reply_to, correlation_id=correlation_id)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            await self.unsubscribe(reply_to, _collector)

    # ── Internal Engine ─────────────────────────────────────────────────────────

    async def _deliver(self, envelope: EventEnvelope, callback: Callable):
        """Entry point for message delivery, triggered by the Driver.

        Returns the delivery task so distributed drivers can await handler
        completion before acknowledging to the broker (crash-safe delivery).
        """
        task = asyncio.create_task(self._do_deliver(envelope, callback))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def _do_deliver(self, envelope: EventEnvelope, callback: Callable):
        sub_name = self._get_name(callback)

        # Chaos/ops pause (Issue 34): hold the delivery while this
        # subscriber's owner is paused — BEFORE the TTL check, so a message
        # held past its TTL still expires honestly on resume. Drivers ack
        # only after this task finishes, so a durable transport's reader
        # stops claiming further messages: the backlog accumulates
        # BROKER-side and drains on resume. (In-process: deliveries pile up
        # as pending tasks in this process' memory — gone on a crash, like
        # everything in-process.)
        while any(sub_name == p or sub_name.startswith(p + ".")
                  for p in self._paused_owners):
            await asyncio.sleep(0.2)

        # Feature 1: TTL Check
        if envelope.ttl is not None:
            age = (datetime.now(timezone.utc) - envelope.timestamp).total_seconds()
            if age > envelope.ttl:
                node = TraceNode(
                    kind="delivered", envelope=envelope, subscribers=[sub_name],
                    success=False, error="ttl_expired", attempts=0
                )
                self._trace_log.append(node)
                return

        # Feature 2: Resolve Subscription Options
        options = self._sub_options.get((envelope.event, callback)) or SubOptions()

        t1 = current_event_id_var.set(envelope.id)
        t2 = current_identity_var.set(sub_name)
        
        success = False
        last_error = None
        attempts = 0
        
        try:
            # Retry Loop
            while attempts <= options.retries:
                attempts += 1
                try:
                    if inspect.iscoroutinefunction(callback):
                        result = await callback(envelope)
                    else:
                        # stdlib, not starlette: the bus must not depend on
                        # the HTTP tool's framework — they are swapped
                        # separately. to_thread copies the context, which
                        # the event-id and identity vars ride on.
                        result = await asyncio.to_thread(callback, envelope)

                    if envelope.reply_to and result is not None:
                        await self.publish(
                            envelope.reply_to, 
                            result if isinstance(result, dict) else {"result": result},
                            correlation_id=envelope.correlation_id
                        )
                    
                    success = True
                    self._consecutive_failures.pop((sub_name, envelope.event), None)
                    break
                except Exception as e:
                    last_error = e
                    if attempts <= options.retries:
                        wait = options.backoff * (2 ** (attempts - 1))
                        await asyncio.sleep(wait)
            
            # Record Trace Node (delivered)
            node = TraceNode(
                kind="delivered", envelope=envelope, subscribers=[sub_name],
                success=success, error=str(last_error) if not success else None,
                attempts=attempts
            )
            self._trace_log.append(node)

            if not success:
                await self._handle_final_failure(last_error, sub_name, envelope, callback, attempts)

        finally:
            current_event_id_var.reset(t1)
            current_identity_var.reset(t2)

    async def _handle_final_failure(self, e, sub_name, envelope, callback, attempts):
        # Poisoned-handler logic
        fail_key = (sub_name, envelope.event)
        count = self._consecutive_failures.get(fail_key, 0) + 1
        self._consecutive_failures[fail_key] = count
        print(f"[EventBus] 💥 Final failure in {sub_name} for event {envelope.event}: {e} ({count}/{self._MAX_CONSECUTIVE_FAILURES})")
        
        if count >= self._MAX_CONSECUTIVE_FAILURES:
            self._consecutive_failures.pop(fail_key, None)
            await self._driver.unsubscribe(envelope.event, callback)
            self._sub_options.pop((envelope.event, callback), None)
            # Make the silent drop observable. Guard: a dropped subscriber OF
            # this very event must not re-trigger it (self-reference loop).
            if envelope.event != self.SUBSCRIBER_DROPPED_EVENT:
                await self.publish(self.SUBSCRIBER_DROPPED_EVENT, {
                    "event": envelope.event,
                    "subscriber": sub_name,
                    "error": str(e),
                    "consecutive_failures": count,
                })

        # Notify failure listeners
        failure_record = {"event": envelope.event, "event_id": envelope.id, "subscriber": sub_name, "error": str(e), "attempts": attempts}
        for fl in self._failure_listeners:
            try: 
                res = fl(failure_record)
                if inspect.isawaitable(res):
                    task = asyncio.create_task(res)
                    self._pending_tasks.add(task)
                    task.add_done_callback(self._pending_tasks.discard)
            except Exception: pass

        # Feature 3: Dead-Letter Queue (DLQ)
        if not envelope.event.startswith(("_dlq.", "_reply.")):
            if os.getenv("EVENT_BUS_DLQ_ENABLED", "true").lower() == "true":
                dlq_payload = {
                    "original": envelope.model_dump(mode="json"),
                    "subscriber": sub_name,
                    "error": str(e),
                    "attempts": attempts,
                    "failed_at": datetime.now(timezone.utc).isoformat()
                }
                await self.publish(f"_dlq.{envelope.event}", dlq_payload, correlation_id=envelope.correlation_id)

    def get_trace_history(self) -> List[TraceNode]: return list(self._trace_log)
    def add_listener(self, cb): self._listeners.append(cb)
    def add_failure_listener(self, cb): self._failure_listeners.append(cb)

    def _get_name(self, cb):
        # This name doubles as the derived consumer group, so it must be
        # stable across replicas AND unique across domains (two domains may
        # declare same-named plugin classes — a bare "Class.method" would
        # collide them into one group and split each other's events).
        owner = getattr(cb, "__self__", None)
        if owner is not None:
            # Kernel-stamped identity ("users.WelcomeServicePlugin") when
            # present; module-qualified fallback otherwise (both stable:
            # domain and module are derived from the file path).
            base = getattr(owner, "_identity", None)
            if not base:
                cls = owner.__class__
                base = f"{cls.__module__}.{cls.__name__}"
            return f"{base}.{cb.__name__}"
        module = getattr(cb, "__module__", None) or "anonymous"
        return f"{module}.{getattr(cb, '__qualname__', 'anonymous')}"

    def get_subscribers(self) -> dict:
        return self._driver.get_status(name_resolver=self._get_name)

    async def shutdown(self):
        if self._pending_tasks:
            tasks = list(self._pending_tasks)
            self._pending_tasks.clear()
            print(f"[EventBus] Cleaning up {len(tasks)} pending tasks...")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if hasattr(self, "_driver") and self._driver is not None:
            await self._driver.shutdown()

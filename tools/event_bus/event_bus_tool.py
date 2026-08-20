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
- key: String. Strict ordering (Kafka/SQS).
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
       ttl → broker-side expiration. For delay, declare a capability claim:
       `capabilities = {"delay": "native", ...}` means YOUR publish()
       persists the delayed envelope broker-side (crash-safe); leave the
       default "in_bus" and the Bus sleeps the delay for you
       (publisher-memory only — a crash during the wait loses the event).
    3. On message arrival, deserialize with self._envelope_cls (injected by
       the Bus via bind() — do NOT import EventEnvelope yourself: the Kernel
       loads modules by path, so an imported copy is a DIFFERENT class and
       Pydantic tracing would reject it) and call
       self._deliver_hook(envelope, callback) — the Bus takes
       over from there (retries, DLQ, tracing all still work).
    4. Install it the same way tools are swapped — file placement, no code
       edits: drop the driver in as tools/event_bus/{name}_driver.py and set
       EVENT_BUS_DRIVER={name}. Discovery is generic (ready-made drivers ship
       in extras/available_tools/, e.g. rabbitmq). Explicit injection also
       works: EventBusTool(driver=KafkaDriver()).
    5. It MUST pass the parity suite: tests/tools/test_event_bus_broker_parity.py.

Plugins are unaffected: same envelope, same API, same semantics.
"""

import collections
import importlib
import uuid
import asyncio
import inspect
import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Dict, List, Tuple, Set
from pydantic import BaseModel, Field, ConfigDict
from starlette.concurrency import run_in_threadpool
from core.base_tool import BaseTool
from core.context import current_event_id_var, current_identity_var


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


class EventBusDriver:
    """Interface for all transport implementations (Translators)."""

    # Capability claims. The contract is semantic; the implementation is
    # free: a driver may implement a Bus semantic with its broker's native
    # machinery — as long as the parity suite still passes. "in_bus" = the
    # Bus runs the universal software fallback; "native" = the driver takes
    # over. Today only "delay" switches behavior: a native delay is
    # broker-persisted and SURVIVES a publisher crash, while the in_bus
    # fallback sleeps in the publisher's memory. "retries" and "dlq" stay
    # in_bus by design (they are already crash-safe: drivers ack only after
    # the handler + retries finish, so a dead replica's message redelivers).
    capabilities: Dict[str, str] = {"delay": "in_bus", "retries": "in_bus", "dlq": "in_bus"}

    async def setup(self): pass
    def bind(self, deliver_hook: Callable, envelope_cls: Optional[type] = None):
        """Injected by the Bus to handle message delivery.

        envelope_cls is the Bus's OWN EventEnvelope class: drivers must
        deserialize with it (self._envelope_cls.model_validate_json) instead
        of importing EventEnvelope, so envelopes always validate against the
        exact class the Bus uses for tracing.
        """
        self._deliver_hook = deliver_hook
        self._envelope_cls = envelope_cls or EventEnvelope

    async def publish(self, envelope: EventEnvelope) -> None: 
        """Pure fire-and-forget transport. Returns nothing."""
        raise NotImplementedError()

    async def subscribe(self, event_name: str, group: Optional[str], callback: Callable): raise NotImplementedError()
    async def unsubscribe(self, event_name: str, callback: Callable): raise NotImplementedError()
    async def unsubscribe_all(self, callback: Callable): raise NotImplementedError()
    def get_status(self, name_resolver: Callable) -> dict: return {"status": "abstract"}
    async def shutdown(self): pass


class InProcessDriver(EventBusDriver):
    """Memory transport. Simulates groups and handles internal delays."""
    def __init__(self):
        self._groups: Dict[str, Dict[Optional[str], List[Callable]]] = {}
        self._indices: Dict[str, Dict[Optional[str], int]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, envelope: EventEnvelope) -> None:
        # Delay is handled by the Bus fallback (capabilities: delay=in_bus).
        # 1. Resolve Targets (Logic moved to Driver side)
        targets = []
        async with self._lock:
            if envelope.event in self._groups:
                for group_name, callbacks in self._groups[envelope.event].items():
                    if not callbacks: continue
                    if group_name is None:
                        targets.extend(callbacks)
                    else:
                        idx = self._indices[envelope.event].get(group_name, 0)
                        targets.append(callbacks[idx % len(callbacks)])
                        self._indices[envelope.event][group_name] = (idx + 1) % len(callbacks)

        # 2. Trigger Delivery Hook (Inversion of Control)
        for cb in targets:
            # We don't await here; the driver schedules the delivery
            asyncio.create_task(self._deliver_hook(envelope, cb))

    async def subscribe(self, event_name: str, group: Optional[str], callback: Callable):
        async with self._lock:
            self._groups.setdefault(event_name, {}).setdefault(group, []).append(callback)
            self._indices.setdefault(event_name, {}).setdefault(group, 0)

    async def unsubscribe(self, event_name: str, callback: Callable):
        async with self._lock:
            self._remove_callback(event_name, callback)

    async def unsubscribe_all(self, callback: Callable):
        async with self._lock:
            for event in list(self._groups.keys()):
                self._remove_callback(event, callback)

    def _remove_callback(self, event_name: str, callback: Callable):
        group_map = self._groups.get(event_name)
        if not group_map: return
        for g_name in list(group_map.keys()):
            group_map[g_name] = [cb for cb in group_map[g_name] if cb != callback]
            if not group_map[g_name]:
                del group_map[g_name]
                if event_name in self._indices and g_name in self._indices[event_name]:
                    del self._indices[event_name][g_name]
        if not group_map:
            del self._groups[event_name]

    def get_status(self, name_resolver: Callable) -> dict:
        return {
            event: [name_resolver(cb) for g in groups.values() for cb in g] 
            for event, groups in self._groups.items()
        }


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
        self._sub_options: Dict[Tuple[str, Callable], SubOptions] = {}

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
            # Base matched by NAME: the Kernel loads this file by path, so the
            # driver's imported EventBusDriver may be a different class object.
            if (isinstance(obj, type) and obj.__module__ == module.__name__
                    and any(b.__name__ == "EventBusDriver" for b in obj.__mro__[1:])):
                return obj()
        raise ValueError(
            f"tools/event_bus/{name}_driver.py defines no EventBusDriver subclass."
        )

    @property
    def name(self) -> str: return "event_bus"

    async def setup(self) -> None:
        await self._driver.setup()
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
        
        CRITICAL: Subscribing callbacks receive an 'EventEnvelope' object.
        Example: async def on_event(self, event: EventEnvelope): print(event.payload)
        
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

        WELL-KNOWN EVENTS:
        - "overlay.vars.set" — publish a flat dict of variables to push them to all
          live overlays (OBS browser sources). Persisted in the overlay_vars table,
          broadcast instantly via SSE, readable in overlay JS as data.stats[key].
          Example: await self.bus.publish("overlay.vars.set", {{"juego.actual": "Elden Ring"}})
          Subscribers receive it like any other event: async def on_event(self, event: EventEnvelope)
          reads the variables from event.payload.

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

        # 2. Hand over to Driver (Fire and Forget)
        task = asyncio.create_task(self._transport_publish(envelope))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _transport_publish(self, envelope: EventEnvelope) -> None:
        """Universal software fallbacks + hand-off to the driver.

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
                        result = await run_in_threadpool(callback, envelope)

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
            print(f"[EventBus] Cleaning up {len(self._pending_tasks)} pending tasks...")
            for task in self._pending_tasks:
                task.cancel()
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        await self._driver.shutdown()

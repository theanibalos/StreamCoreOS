"""
In-Memory Event Bus — Reference Implementation for MicroCoreOS
===============================================================

This is the REFERENCE IMPLEMENTATION for event bus tools in MicroCoreOS.
Any new event bus tool (Redis Streams, RabbitMQ, Kafka) MUST follow this contract.

PUBLIC CONTRACT (what plugins use):
────────────────────────────────────────────────────────────────────────────────

    # Pub/Sub — fire-and-forget side effects
    await bus.publish("user.created", {"id": 42, "email": "a@b.com"})
    await bus.subscribe("user.created", self.on_user_created)
    await bus.unsubscribe("user.created", self.on_user_created)

    # Request/Response — Async RPC for mandatory cross-domain queries
    # WARNING: Abuse reintroduces coupling. Use only when a response is strictly required.
    response = await bus.request("user.validate", {"email": "a@b.com"}, timeout=5)

    # Observability
    history = bus.get_trace_history()   # → list of last 500 event records


SUBSCRIBER SIGNATURE:
────────────────────────────────────────────────────────────────────────────────

    # For publish() — return value is ignored
    async def on_user_created(self, data: dict) -> None:
        user_id = data.get("id")
        ...

    # For request() — subscriber MUST return a non-None value
    async def on_user_validate(self, data: dict) -> dict:
        exists = await self.db.query_one("SELECT 1 FROM users WHERE email = $1", [data["email"]])
        return {"exists": exists is not None}

    # Sync handlers are also supported — the bus offloads them to a thread pool
    def on_user_created_sync(self, data: dict) -> None:
        ...


WILDCARD SUBSCRIPTION:
────────────────────────────────────────────────────────────────────────────────

    await bus.subscribe("*", self.monitor_all)

    # Wildcard subscribers receive all events but do NOT participate in RPC replies.
    # Intended for observability, logging, and monitoring plugins.
    async def monitor_all(self, data: dict) -> None:
        ...


CAUSALITY TRACKING:
────────────────────────────────────────────────────────────────────────────────

    The bus automatically propagates ContextVars into each subscriber's execution context:
        - current_event_id_var  → the ID of the triggering event
        - current_identity_var  → the name of the subscriber class and method

    No manual effort is required. The `logger` tool reads these automatically.


REPLACEMENT STANDARD (implement this to swap the backend):
────────────────────────────────────────────────────────────────────────────────

    To create a Redis Streams implementation:

    1. Create tools/redis_event_bus/redis_event_bus_tool.py
    2. name = "event_bus"                         ← same injection key, plugins are unaffected
    3. Implement the 4 public methods:
          async publish(event_name, data)
          async subscribe(event_name, callback)
          async unsubscribe(event_name, callback)
          async request(event_name, data, timeout)
    4. Subscriber callback signature: async def handler(data: dict)
    5. Causality: propagate context vars into the subscriber's asyncio context
       (e.g., copy the current context with contextvars.copy_context())
    6. For request/response: use a unique reply stream per call (e.g., XREAD with a temp key)

    Plugins will NOT require any changes.
"""

import collections
import time
import uuid
import asyncio
import inspect
from starlette.concurrency import run_in_threadpool
from core.base_tool import BaseTool
from core.context import current_event_id_var, current_identity_var


class EventBusTool(BaseTool):

    _MAX_CONSECUTIVE_FAILURES = 5

    def __init__(self):
        self._subscribers: dict[str, list] = {}
        self._lock = asyncio.Lock()
        self._trace_log: collections.deque = collections.deque(maxlen=500)
        self._listeners: list = []          # sink: called with trace record on every event
        self._failure_listeners: list = []  # sink: called when a subscriber raises
        self._consecutive_failures: dict[str, int] = {}  # subscriber_name → consecutive failure count

    @property
    def name(self) -> str:
        return "event_bus"

    async def setup(self) -> None:
        print("[System] EventBusTool: Online.")

    def get_interface_description(self) -> str:
        return """
        Async Event Bus Tool (event_bus):
        - PURPOSE: Non-blocking communication between plugins. Pub/Sub and Async RPC.
        - SUBSCRIBER SIGNATURE: async def handler(self, data: dict)
        - CAPABILITIES:
            - await publish(event_name, data): Fire-and-forget broadcast.
            - await subscribe(event_name, callback): Register a subscriber.
                Use event_name='*' for wildcard (observability only, no RPC).
            - await unsubscribe(event_name, callback): Remove a subscriber.
            - await request(event_name, data, timeout=5): Async RPC.
                The subscriber must return a non-None dict.
            - get_trace_history() -> list: Last 500 event records with causality data.
            - get_subscribers() -> dict: Current subscriber map {event_name: [subscriber_names]}.
            - add_listener(callback): Sink pattern — called with full trace record on every event.
                Signature: callback(record: dict) — record has: id, event, emitter, subscribers, payload_keys, timestamp.
                Use for real-time observability (e.g. WebSocket broadcast). Non-blocking.
            - add_failure_listener(callback): Sink called when a subscriber raises during dispatch.
                Signature: callback(record: dict) — record has: event, event_id, subscriber, error.
                Use to implement dead-letter alerting. Non-blocking — keep it fast.
        """

    # ── Public API ──────────────────────────────────────────────────────────────

    async def subscribe(self, event_name: str, callback) -> None:
        async with self._lock:
            self._subscribers.setdefault(event_name, []).append(callback)

    async def unsubscribe(self, event_name: str, callback) -> None:
        async with self._lock:
            if event_name in self._subscribers:
                self._subscribers[event_name] = [
                    cb for cb in self._subscribers[event_name] if cb is not callback
                ]
                if not self._subscribers[event_name]:
                    del self._subscribers[event_name]

    async def publish(self, event_name: str, data: dict) -> None:
        """
        Broadcasts an event to all subscribers. Non-blocking — returns immediately.
        Each subscriber runs in its own asyncio Task and is monitored for failures.
        """
        emitter = current_identity_var.get()
        direct_cbs, wildcard_cbs = await self._collect_callbacks(event_name)

        event_id = str(uuid.uuid4())
        parent_id = current_event_id_var.get()

        all_callbacks = direct_cbs + wildcard_cbs
        self._record_trace(event_id, parent_id, event_name, emitter, data, all_callbacks)

        for cb in direct_cbs:
            task = asyncio.create_task(
                self._dispatch(cb, data, event_name, event_id)
            )
            self._monitor_task(task, event_name, cb)

        # Wildcard callbacks get enriched data with _event_type
        wildcard_data = {**data, "_event_type": event_name} if isinstance(data, dict) else data
        for cb in wildcard_cbs:
            task = asyncio.create_task(
                self._dispatch(cb, wildcard_data, event_name, event_id)
            )
            self._monitor_task(task, event_name, cb)

    async def request(self, event_name: str, data: dict, timeout: float = 5):
        """
        Async RPC: publishes an event and waits for the first subscriber to return a value.

        The responding subscriber must return a non-None dict.
        Wildcard ('*') subscribers observe the event but cannot reply.
        Raises asyncio.TimeoutError if no response arrives within the timeout.
        """
        emitter = current_identity_var.get()
        reply_channel = f"_reply.{event_name}.{uuid.uuid4().hex[:8]}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        async def _reply_collector(reply_data: dict) -> None:
            if not future.done():
                future.set_result(reply_data)

        await self.subscribe(reply_channel, _reply_collector)
        try:
            direct_cbs, wildcard_cbs = await self._collect_callbacks(event_name)

            event_id = str(uuid.uuid4())
            parent_id = current_event_id_var.get()
            self._record_trace(event_id, parent_id, event_name, emitter, data, direct_cbs + wildcard_cbs)

            # Direct subscribers participate in the RPC (can reply)
            for cb in direct_cbs:
                task = asyncio.create_task(
                    self._dispatch(cb, data, event_name, event_id, reply_channel=reply_channel)
                )
                self._monitor_task(task, event_name, cb)

            # Wildcard subscribers observe only — their return value is ignored
            wildcard_data = {**data, "_event_type": event_name} if isinstance(data, dict) else data
            for cb in wildcard_cbs:
                task = asyncio.create_task(
                    self._dispatch(cb, wildcard_data, event_name, event_id)
                )
                self._monitor_task(task, event_name, cb)

            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            await self.unsubscribe(reply_channel, _reply_collector)

    def get_trace_history(self) -> list:
        """Returns a snapshot of the last 500 event records (thread-safe copy)."""
        return list(self._trace_log)

    def add_listener(self, callback) -> None:
        """
        Registers a sink that receives the full trace record on every event.
        Signature: callback(record: dict) where record = {id, event, emitter, subscribers, payload_keys, timestamp}
        Intended for observability (e.g. WebSocket broadcast). Non-blocking.
        """
        if callback not in self._listeners:
            self._listeners.append(callback)

    def add_failure_listener(self, callback) -> None:
        """
        Registers a sink called when a subscriber raises an exception during dispatch.
        Signature: callback(record: dict) where record = {event, subscriber, error, event_id}.
        Use to implement dead-letter alerting or delivery guarantees.
        Non-blocking — keep the callback fast.
        """
        if callback not in self._failure_listeners:
            self._failure_listeners.append(callback)

    def get_subscribers(self) -> dict:
        """Returns the current subscriber map: {event_name: [subscriber_names]}."""
        return {
            event: [self._get_name(cb) for cb in cbs]
            for event, cbs in self._subscribers.items()
        }

    # ── Internal dispatch pipeline ───────────────────────────────────────────────

    async def _collect_callbacks(self, event_name: str) -> tuple[list, list]:
        async with self._lock:
            direct = list(self._subscribers.get(event_name, []))
            wildcard = list(self._subscribers.get("*", []))
        return direct, wildcard

    async def _dispatch(
        self,
        callback,
        data: dict,
        event_name: str,
        event_id: str,
        reply_channel: str = None,
    ) -> None:
        """
        Executes a single subscriber callback inside its own causality context.

        - Sets current_event_id_var and current_identity_var for the duration of the call.
        - Supports both async and sync callbacks (sync is offloaded to a thread pool).
        - If reply_channel is set and the callback returns a value, the reply is dispatched.
        """
        subscriber_name = self._get_name(callback)
        id_token = current_event_id_var.set(event_id)
        ident_token = current_identity_var.set(subscriber_name)
        try:
            if inspect.iscoroutinefunction(callback):
                result = await callback(data)
            else:
                result = await run_in_threadpool(callback, data)

            if reply_channel is not None and result is not None:
                await self._dispatch_reply(reply_channel, result, subscriber_name)

            # Reset consecutive failure count on success
            self._consecutive_failures.pop(subscriber_name, None)

        except Exception as e:
            count = self._consecutive_failures.get(subscriber_name, 0) + 1
            self._consecutive_failures[subscriber_name] = count
            print(f"[EventBus] ⚠️  '{subscriber_name}' failed handling '{event_name}': {e} (failure {count}/{self._MAX_CONSECUTIVE_FAILURES})")

            if count >= self._MAX_CONSECUTIVE_FAILURES:
                asyncio.create_task(self._auto_unsubscribe_dead(callback, subscriber_name))

            failure_record = {
                "event": event_name,
                "event_id": event_id,
                "subscriber": subscriber_name,
                "error": str(e),
            }
            for fl in self._failure_listeners:
                try:
                    fl(failure_record)
                except Exception as fe:
                    print(f"[EventBus] Failure listener error: {fe}")
        finally:
            current_event_id_var.reset(id_token)
            current_identity_var.reset(ident_token)

    async def _dispatch_reply(self, reply_channel: str, result, emitter: str) -> None:
        """Sends the subscriber's return value to the reply channel."""
        reply_data = result if isinstance(result, dict) else {"result": result}
        reply_cbs, _ = await self._collect_callbacks(reply_channel)
        event_id = str(uuid.uuid4())
        for cb in reply_cbs:
            task = asyncio.create_task(
                self._dispatch(cb, reply_data, reply_channel, event_id)
            )
            self._monitor_task(task, reply_channel, cb)

    # ── Observability ────────────────────────────────────────────────────────────

    def _record_trace(
        self,
        event_id: str,
        parent_id,
        event_name: str,
        emitter,
        data: dict,
        callbacks: list,
    ) -> None:
        record = {
            "id": event_id,
            "parent_id": parent_id,
            "timestamp": time.time(),
            "event": event_name,
            "emitter": emitter,
            "subscribers": list({self._get_name(cb) for cb in callbacks}),
            "payload_keys": list(data.keys()) if isinstance(data, dict) else [],
        }
        self._trace_log.append(record)
        for listener in self._listeners:
            try:
                listener(record)
            except Exception as e:
                print(f"[EventBus] Listener failure: {e}")
        print(
            f"[EventBus] 📣 {event_name}"
            f"  id={event_id[:8]}"
            f"  parent={str(parent_id)[:8] if parent_id else 'root'}"
            f"  from={emitter}"
        )

    async def _auto_unsubscribe_dead(self, callback, subscriber_name: str) -> None:
        """Removes a consistently-failing handler from all events it is subscribed to."""
        async with self._lock:
            affected = [
                event for event, cbs in self._subscribers.items()
                if callback in cbs
            ]
            for event in affected:
                self._subscribers[event] = [cb for cb in self._subscribers[event] if cb is not callback]
                if not self._subscribers[event]:
                    del self._subscribers[event]
        self._consecutive_failures.pop(subscriber_name, None)
        print(f"[EventBus] 🔇 Auto-unsubscribed dead handler '{subscriber_name}' from: {affected}")

    def _monitor_task(self, task: asyncio.Task, event_name: str, callback) -> None:
        """Attaches a done-callback to report unhandled Task failures to stdout."""
        name = self._get_name(callback)
        def _on_done(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception():
                print(
                    f"[EventBus] 💥 Unhandled Task failure"
                    f"  subscriber='{name}'"
                    f"  event='{event_name}'"
                    f"  error={t.exception()}"
                )
        task.add_done_callback(_on_done)

    # ── Utilities ────────────────────────────────────────────────────────────────

    def _get_name(self, callback) -> str:
        if hasattr(callback, "__self__"):
            return f"{callback.__self__.__class__.__name__}.{callback.__name__}"
        if hasattr(callback, "__qualname__"):
            return callback.__qualname__
        return "anonymous"

    async def shutdown(self) -> None:
        pass

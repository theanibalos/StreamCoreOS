"""
Enterprise Event Bus — Driver Interface & In-Process Reference Driver
======================================================================
EventBusDriver is the transport interface (see event_bus_tool.py's module
docstring for the full REPLACEMENT STANDARD contract) and InProcessDriver is
its in-memory reference implementation. Split out of event_bus_tool.py.
"""

import asyncio
from typing import Callable, Optional, Dict, List
from tools.event_bus.envelope import EventEnvelope


class EventBusDriver:
    """Interface for all transport implementations (Translators)."""

    # Capability claims (Issue 30). The contract is semantic; the
    # implementation is free: a driver may implement a Bus semantic with its
    # broker's native machinery — as long as the parity suite still passes.
    # "in_bus" = the Bus runs the universal software fallback; "native" = the
    # driver takes over. Today only "delay" switches behavior: a native delay
    # is broker-persisted and SURVIVES a publisher crash, while the in_bus
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

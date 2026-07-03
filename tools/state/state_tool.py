"""
State Tool — Reference Implementation for Key-Value State in MicroCoreOS
=========================================================================

This is the REFERENCE IMPLEMENTATION for shared-state tools in MicroCoreOS.
Any distributed replacement (Redis, Valkey, Memcached, ...) MUST follow this
contract. The API is async by design: an in-memory store resolves instantly,
but a network-backed store must never block the event loop.

PUBLIC CONTRACT (what plugins use):
─────────────────────────────────────────────────────────────────
    await state.set("key", value, namespace="cache", ttl=300)
    value   = await state.get("key", default=None, namespace="cache")
    exists  = await state.has("key", namespace="cache")
    names   = await state.keys(namespace="cache")
    snap    = await state.get_all(namespace="cache")
    count   = await state.increment("hits", amount=1, namespace="cache", ttl=60)
    await state.delete("key", namespace="cache")
    await state.clear(namespace="cache")

VALUES: must be JSON-serializable (str, int, float, bool, None, list, dict).
        The in-memory store accepts any Python object, but a distributed
        replacement will serialize — non-JSON values break the swap.

TTL (seconds):
    - set(ttl=N): the key expires N seconds after the write. ttl=None → no expiry.
    - increment(ttl=N): the TTL is applied ONLY when the key is created
      (or had expired). Subsequent increments do NOT extend it — this gives
      fixed-window semantics and maps to Redis `INCR` + `EXPIRE NX`.
    - Expired keys behave exactly like missing keys.

REPLACEMENT STANDARD (e.g. Redis — same name "state", plugins unaffected):
    namespace        → key prefix ("{namespace}:{key}")
    set + ttl        → SET key value EX ttl
    get / has        → GET / EXISTS
    increment + ttl  → INCRBY + EXPIRE key ttl NX
    keys / get_all   → SCAN with prefix match
    delete / clear   → DEL / SCAN+DEL
"""

import time
import copy
import threading
from core.base_tool import BaseTool

_NO_EXPIRY = None


class StateTool(BaseTool):
    """
    In-Memory State Tool (StateTool):
    Allows sharing volatile global data between plugins safely.
    Ideal for: counters, temporary caches, and business semaphores.
    """

    def __init__(self):
        # namespace -> key -> (value, expires_at | None)
        self._state = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "state"

    def setup(self):
        print("[System] StateTool: In-memory store ready and thread-safe.")

    def get_interface_description(self) -> str:
        return """
        Key-Value State Tool (state):
        - PURPOSE: Share volatile global data between plugins safely.
        - IDEAL FOR: Counters, temporary caches, rate-limit windows, business semaphores.
        - CONTRACT: All methods are async. Values must be JSON-serializable so the
          tool can be swapped for a distributed store (Redis) without touching plugins.
        - TTL: optional expiry in seconds. Expired keys behave like missing keys.
          On increment(), the TTL only applies when the key is created (fixed window).
        - CAPABILITIES:
            - await set(key, value, namespace='default', ttl=None): Store a value.
            - await get(key, default=None, namespace='default'): Retrieve a value (None if missing).
            - await has(key, namespace='default'): Returns True if key exists.
            - await keys(namespace='default'): Returns list of all live keys in the namespace.
            - await get_all(namespace='default'): Returns a deep copy of all live key-value pairs.
            - await increment(key, amount=1, namespace='default', ttl=None): Atomic increment.
              Starts at 0. Returns the new value.
            - await delete(key, namespace='default'): Delete a key (no-op if missing).
            - await clear(namespace='default'): Remove all keys in the namespace.
        """

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_ns(self, namespace: str) -> dict:
        if namespace not in self._state:
            self._state[namespace] = {}
        return self._state[namespace]

    def _live_value(self, namespace: str, key: str, default=None):
        """Return the value if the entry exists and is not expired; else default.
        Purges the entry lazily when expired. Caller must hold the lock."""
        ns = self._state.get(namespace, {})
        entry = ns.get(key)
        if entry is None:
            return default
        value, expires_at = entry
        if expires_at is not _NO_EXPIRY and time.monotonic() >= expires_at:
            ns.pop(key, None)
            return default
        return value

    def _expires_at(self, ttl: float | None):
        return time.monotonic() + ttl if ttl is not None else _NO_EXPIRY

    # ── Public API (async contract) ────────────────────────────────────────────

    async def set(self, key: str, value, namespace: str = "default", ttl: float | None = None) -> None:
        with self._lock:
            self._get_ns(namespace)[key] = (value, self._expires_at(ttl))

    async def get(self, key: str, default=None, namespace: str = "default"):
        with self._lock:
            return self._live_value(namespace, key, default)

    async def has(self, key: str, namespace: str = "default") -> bool:
        sentinel = object()
        with self._lock:
            return self._live_value(namespace, key, sentinel) is not sentinel

    def _purge_expired(self, namespace: str) -> dict:
        """Drop expired entries and return the namespace dict. Caller must hold the lock."""
        ns = self._state.get(namespace, {})
        now = time.monotonic()
        for k in list(ns.keys()):
            expires_at = ns[k][1]
            if expires_at is not _NO_EXPIRY and now >= expires_at:
                ns.pop(k)
        return ns

    async def keys(self, namespace: str = "default") -> list:
        with self._lock:
            return list(self._purge_expired(namespace).keys())

    async def get_all(self, namespace: str = "default") -> dict:
        with self._lock:
            ns = self._purge_expired(namespace)
            return copy.deepcopy({k: v for k, (v, _) in ns.items()})

    async def increment(self, key: str, amount: int | float = 1,
                        namespace: str = "default", ttl: float | None = None) -> int | float:
        sentinel = object()
        with self._lock:
            ns = self._get_ns(namespace)
            current = self._live_value(namespace, key, sentinel)
            if current is sentinel:
                # Key is new (or expired): start at 0 and apply the TTL now.
                ns[key] = (amount, self._expires_at(ttl))
                return amount
            if not isinstance(current, (int, float)) or isinstance(current, bool):
                raise ValueError(f"Key '{key}' is not numeric.")
            # Existing key: keep its original expiry (fixed-window semantics).
            ns[key] = (current + amount, ns[key][1])
            return current + amount

    async def delete(self, key: str, namespace: str = "default") -> None:
        with self._lock:
            self._state.get(namespace, {}).pop(key, None)

    async def clear(self, namespace: str = "default") -> None:
        with self._lock:
            if namespace in self._state:
                self._state[namespace].clear()

    def shutdown(self):
        with self._lock:
            self._state.clear()

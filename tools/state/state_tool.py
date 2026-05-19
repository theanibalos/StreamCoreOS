import threading
import copy
from core.base_tool import BaseTool


class StateTool(BaseTool):
    """
    In-Memory State Tool (StateTool):
    Allows sharing volatile global data between plugins safely.
    Ideal for: counters, temporary caches, and business semaphores.
    """

    def __init__(self):
        self._state = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "state"

    def setup(self):
        print("[System] StateTool: In-memory store ready and thread-safe.")

    def get_interface_description(self) -> str:
        return """
        In-Memory State Tool (state):
        - PURPOSE: Share volatile global data between plugins safely.
        - IDEAL FOR: Counters, temporary caches, and shared business semaphores.
        - CAPABILITIES:
            - set(key, value, namespace='default'): Store a value.
            - get(key, default=None, namespace='default'): Retrieve a value (None if missing).
            - has(key, namespace='default'): Returns True if key exists.
            - keys(namespace='default'): Returns list of all keys in the namespace.
            - get_all(namespace='default'): Returns a shallow copy of all key-value pairs.
            - increment(key, amount=1, namespace='default'): Atomic increment. Starts at 0.
            - delete(key, namespace='default'): Delete a key (no-op if missing).
            - clear(namespace='default'): Remove all keys in the namespace.
        """

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_ns(self, namespace: str) -> dict:
        if namespace not in self._state:
            self._state[namespace] = {}
        return self._state[namespace]

    def _get_ns_readonly(self, namespace: str) -> dict:
        return self._state.get(namespace, {})

    # ── Public API ─────────────────────────────────────────────────────────────

    def set(self, key: str, value, namespace: str = "default") -> None:
        with self._lock:
            self._get_ns(namespace)[key] = value

    def get(self, key: str, default=None, namespace: str = "default"):
        with self._lock:
            return self._get_ns_readonly(namespace).get(key, default)

    def has(self, key: str, namespace: str = "default") -> bool:
        with self._lock:
            return key in self._get_ns_readonly(namespace)

    def keys(self, namespace: str = "default") -> list:
        with self._lock:
            return list(self._get_ns_readonly(namespace).keys())

    def get_all(self, namespace: str = "default") -> dict:
        with self._lock:
            return copy.deepcopy(self._get_ns_readonly(namespace))

    def increment(self, key: str, amount: int | float = 1, namespace: str = "default") -> int | float:
        with self._lock:
            ns = self._get_ns(namespace)
            current = ns.get(key, 0)
            if not isinstance(current, (int, float)):
                raise ValueError(f"Key '{key}' is not numeric.")
            ns[key] = current + amount
            return ns[key]

    def delete(self, key: str, namespace: str = "default") -> None:
        with self._lock:
            self._get_ns_readonly(namespace).pop(key, None)

    def clear(self, namespace: str = "default") -> None:
        with self._lock:
            if namespace in self._state:
                self._state[namespace].clear()

    def shutdown(self):
        with self._lock:
            self._state.clear()

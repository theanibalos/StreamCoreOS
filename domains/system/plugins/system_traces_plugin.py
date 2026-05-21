from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


# ── Modelos de Datos ─────────────────────────────────────────────────────────

class TraceNode(BaseModel):
    id: str
    parent_id: Optional[str] = None
    event: str
    emitter: str
    subscribers: list[str]
    payload_keys: list[str]
    timestamp: float
    children: list["TraceNode"] = []

TraceNode.model_rebuild()

class SystemTracesTreeResponse(BaseModel):
    success: bool
    data: Optional[list[TraceNode]] = None
    error: Optional[str] = None

# Modelo para la vista plana (sin la lista de children)
class TraceFlatNode(BaseModel):
    id: str
    parent_id: Optional[str] = None
    event: str
    emitter: str
    subscribers: list[str]
    payload_keys: list[str]
    timestamp: float

class SystemTracesFlatResponse(BaseModel):
    success: bool
    data: Optional[list[TraceFlatNode]] = None
    error: Optional[str] = None


# ── Plugin ───────────────────────────────────────────────────────────────────

class SystemTracesPlugin(BasePlugin):
    """
    Exposes the event bus trace log in two formats:
    1. Flat list (chronological) for fast logging UI.
    2. Causal tree (hierarchical) for debugging specific flows.
    """

    def __init__(self, http, event_bus):
        self.http = http
        self.event_bus = event_bus

    async def on_boot(self):
        # Tree endpoint
        self.http.add_endpoint(
            "/api/system/traces/tree", "GET", self.get_tree,
            tags=["System"],
            response_model=SystemTracesTreeResponse
        )
        
        # Flat list endpoint
        self.http.add_endpoint(
            "/api/system/traces/flat", "GET", self.get_flat,
            tags=["System"],
            response_model=SystemTracesFlatResponse
        )

    def _get_clean_history(self):
        """Returns trace history with internal RPC reply channels filtered out."""
        history = self.event_bus.get_trace_history()
        return [r for r in history if not r["event"].startswith("_reply.")]

    async def get_flat(self, data: dict, context=None):
        """Returns records in reverse chronological order (newest first)."""
        try:
            records = self._get_clean_history()
            # Sort by timestamp descending
            flat_list = sorted(records, key=lambda x: x["timestamp"], reverse=True)
            return {"success": True, "data": flat_list}
        except Exception as e:
            print(f"[SystemTraces] Error: {e}")
            return {"success": False, "error": "Internal error"}

    async def get_tree(self, data: dict, context=None):
        """Returns records nested as a parent → child causal tree."""
        try:
            records = self._get_clean_history()

            nodes = {
                r["id"]: {
                    "id": r["id"],
                    "parent_id": r.get("parent_id"),
                    "event": r["event"],
                    "emitter": r["emitter"],
                    "subscribers": r["subscribers"],
                    "payload_keys": r["payload_keys"],
                    "timestamp": r["timestamp"],
                    "children": []
                }
                for r in records
            }

            roots = []
            for r in records:
                node_dict = nodes[r["id"]]
                parent_id = r.get("parent_id")

                if parent_id and parent_id in nodes:
                    nodes[parent_id]["children"].append(node_dict)
                else:
                    roots.append(node_dict)

            roots.sort(key=lambda x: x["timestamp"], reverse=True)

            return {"success": True, "data": roots}
        except Exception as e:
            print(f"[SystemTraces] Error: {e}")
            return {"success": False, "error": "Internal error"}

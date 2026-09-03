import asyncio
import json
from microcoreos.base_plugin import BasePlugin


class SystemTracesStreamPlugin(BasePlugin):
    """
    Streams the causal event tree in real time via Server-Sent Events.

    GET /system/traces/stream

    Message types:
      - type: "snapshot" — sent once on connect with the full current tree
      - type: "node"     — sent on every new event with the new node and its parent_id

    Node shape:
      {
        "id": "uuid",
        "parent_id": "uuid | null",
        "event": "user.created",
        "emitter": "users.CreateUserPlugin.execute",
        "subscribers": ["users.WelcomeServicePlugin.on_user_created"],
        "payload_keys": ["id", "email"],
        "timestamp": 1234567890.0,
        "children": []        # only present in snapshot nodes
      }

    Client logic:
      - On "snapshot": render the full tree from data.tree
      - On "node":     find the node whose id == data.node.parent_id, append data.node as a child.
                       If parent not found, treat as a new root.
    """

    def __init__(self, http, event_bus):
        self.http = http
        self.event_bus = event_bus
        self._queues: set = set()

    async def on_boot(self):
        self.event_bus.add_listener(self._on_event)
        self.http.add_sse_endpoint(
            "/api/system/traces/stream",
            generator=self._stream,
            tags=["System"],
        )

    def _get_merged_records(self) -> list[dict]:
        history = self.event_bus.get_trace_history()
        records = [r for r in history if not r.envelope.event.startswith("_reply.")]
        
        merged: dict[str, dict] = {}
        for r in records:
            eid = r.envelope.id
            if eid not in merged:
                env = r.envelope
                merged[eid] = {
                    "id": env.id,
                    "parent_id": env.parent_id,
                    "event": env.event,
                    "emitter": env.emitter,
                    "subscribers": list(r.subscribers) if r.subscribers else [],
                    "payload_keys": list(env.payload.keys()),
                    "timestamp": float(env.timestamp.timestamp()),
                }
            else:
                if r.subscribers:
                    for sub in r.subscribers:
                        if sub not in merged[eid]["subscribers"]:
                            merged[eid]["subscribers"].append(sub)
        return list(merged.values())

    def _on_event(self, record: dict):
        if record["event"].startswith("_reply.") or not self._queues:
            return
        
        node = {
            "id": record["id"],
            "parent_id": record.get("parent_id"),
            "event": record["event"],
            "emitter": record["emitter"],
            "subscribers": record.get("subscribers") or self.event_bus.get_subscribers().get(record["event"], []),
            "payload_keys": record["payload_keys"],
            "timestamp": record["timestamp"],
        }
        
        try:
            loop = asyncio.get_running_loop()
            for q in list(self._queues):
                try:
                    loop.call_soon_threadsafe(q.put_nowait, node)
                except asyncio.QueueFull:
                    pass  # slow consumer — drop event rather than grow unbounded
        except RuntimeError:
            pass

    async def _stream(self, data: dict):
        queue = asyncio.Queue(maxsize=200)
        self._queues.add(queue)
        try:
            history = self._get_merged_records()
            yield f"data: {json.dumps({'type': 'snapshot', 'tree': self._build_tree(history)})}\n\n"

            while True:
                record = await queue.get()
                yield f"data: {json.dumps({'type': 'node', 'node': record})}\n\n"
        finally:
            self._queues.discard(queue)

    def _build_tree(self, records: list) -> list:
        nodes = {r["id"]: {**r, "children": []} for r in records}
        roots = []
        for r in records:
            node = nodes[r["id"]]
            parent_id = r.get("parent_id")
            if parent_id and parent_id in nodes:
                if node not in nodes[parent_id]["children"]:
                    nodes[parent_id]["children"].append(node)
            else:
                if node not in roots:
                    roots.append(node)
        return roots

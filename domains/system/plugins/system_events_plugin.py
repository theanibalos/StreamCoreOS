import os
import re
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class EventEntry(BaseModel):
    event: str
    subscribers: list[str]
    last_emitters: list[str]
    times_fired: int

class SystemEventsData(BaseModel):
    events: list[EventEntry]

class SystemEventsResponse(BaseModel):
    success: bool
    data: Optional[SystemEventsData] = None
    error: Optional[str] = None


class SystemEventsPlugin(BasePlugin):
    """
    Exposes the system's event topology and execution statistics.
    Returns a map of all known events, their subscribers, and firing frequency.
    """

    def __init__(self, http, event_bus):
        self.http = http
        self.event_bus = event_bus

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/system/events", "GET", self.execute,
            tags=["System"],
            response_model=SystemEventsResponse
        )

    def _scan_all_published_events(self) -> set[str]:
        events: set[str] = set()
        domains_dir = os.path.abspath("domains")
        if not os.path.exists(domains_dir):
            return events
        for domain in os.listdir(domains_dir):
            plugins_dir = os.path.join(domains_dir, domain, "plugins")
            if not os.path.isdir(plugins_dir):
                continue
            for filename in os.listdir(plugins_dir):
                if not filename.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(plugins_dir, filename), "r", encoding="utf-8") as f:
                        content = f.read()
                    events.update(re.findall(r'\.publish\(\s*["\']([^"\']+)["\']', content))
                except Exception:
                    pass
        return events

    async def execute(self, data: dict, context=None):
        try:
            subscribers = self.event_bus.get_subscribers()
            history = self.event_bus.get_trace_history()

            stats: dict[str, dict] = {}
            for record in history:
                # The bus logs one "published" node plus one "delivered" node
                # per subscriber; only publications count as firings.
                if record.kind != "published":
                    continue
                name = record.envelope.event
                if name.startswith("_reply."):
                    continue

                if name not in stats:
                    stats[name] = {"emitters": set(), "count": 0}

                stats[name]["emitters"].add(record.envelope.emitter)
                stats[name]["count"] += 1

            all_events = self._scan_all_published_events() | set(subscribers.keys()) | set(stats.keys())
            
            events = [
                EventEntry(
                    event=event,
                    subscribers=subscribers.get(event, []),
                    last_emitters=list(stats.get(event, {}).get("emitters", set())),
                    times_fired=stats.get(event, {}).get("count", 0),
                )
                for event in sorted(all_events)
                if not event.startswith("_reply.")
            ]

            return {"success": True, "data": {"events": events}}
            
        except Exception as e:
            print(f"[SystemEvents] Error: {e}")
            return {"success": False, "error": "Could not retrieve events"}

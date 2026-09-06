import ast
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from microcoreos import BasePlugin


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
        self._static_publishers: Optional[dict[str, list[str]]] = None

    async def on_boot(self):
        self.http.add_endpoint(
            "/system/events", "GET", self.execute,
            tags=["System"],
            response_model=SystemEventsResponse
        )
        self.http.add_endpoint(
            "/api/system/events", "GET", self.execute,
            tags=["System"],
            response_model=SystemEventsResponse
        )

    def _scan_static_publishers(self) -> dict[str, list[str]]:
        """Map event -> publish/request sites ("<domain>.<Class>.<method>") found
        in the plugin sources. Lets last_emitters be populated before any event
        has fired. Cached: sources cannot change within a running process."""
        if self._static_publishers is not None:
            return self._static_publishers

        publishers: dict[str, set[str]] = {}
        domains_dir = os.path.abspath("domains")
        if not os.path.isdir(domains_dir):
            candidate = Path(__file__).resolve().parents[2]
            if candidate.is_dir() and candidate.name == "domains":
                domains_dir = str(candidate)

        if os.path.exists(domains_dir):
            for domain in os.listdir(domains_dir):
                plugins_dir = os.path.join(domains_dir, domain, "plugins")
                if not os.path.isdir(plugins_dir):
                    continue
                for filename in os.listdir(plugins_dir):
                    if not filename.endswith(".py"):
                        continue
                    try:
                        with open(os.path.join(plugins_dir, filename), "r", encoding="utf-8") as f:
                            tree = ast.parse(f.read())
                    except Exception:
                        continue
                    for classdef in tree.body:
                        if not isinstance(classdef, ast.ClassDef):
                            continue
                        for method in classdef.body:
                            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                continue
                            for call in ast.walk(method):
                                if (
                                    isinstance(call, ast.Call)
                                    and isinstance(call.func, ast.Attribute)
                                    and call.func.attr in ("publish", "request")
                                    and call.args
                                    and isinstance(call.args[0], ast.Constant)
                                    and isinstance(call.args[0].value, str)
                                ):
                                    site = f"{domain}.{classdef.name}.{method.name}"
                                    publishers.setdefault(call.args[0].value, set()).add(site)

        self._static_publishers = {ev: sorted(sites) for ev, sites in publishers.items()}
        return self._static_publishers

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

            static_publishers = self._scan_static_publishers()
            all_events = set(static_publishers.keys()) | set(subscribers.keys()) | set(stats.keys())

            # Attributed runtime emitters win; static publish sites fill in for
            # events that have not fired yet so the topology is complete from
            # boot. Anonymous runtime identities ("system"/"Unknown", e.g. a
            # background task without context) never override a static site.
            def emitters_for(event: str) -> list[str]:
                runtime = stats.get(event, {}).get("emitters", set())
                attributed = sorted(em for em in runtime if em not in ("system", "Unknown"))
                return attributed or static_publishers.get(event, []) or sorted(runtime)

            events = [
                EventEntry(
                    event=event,
                    subscribers=subscribers.get(event, []),
                    last_emitters=emitters_for(event),
                    times_fired=stats.get(event, {}).get("count", 0),
                )
                for event in sorted(all_events)
                if not event.startswith("_reply.")
            ]

            return {"success": True, "data": {"events": events}}
            
        except Exception as e:
            print(f"[SystemEvents] Error: {e}")
            return {"success": False, "error": "Could not retrieve events"}

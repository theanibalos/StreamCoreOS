import ast
import os
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

EXCLUDED_PREFIXES = ("_dlq.", "_reply.")


class LintFinding(BaseModel):
    code: str
    severity: str
    event: Optional[str] = None
    publisher: Optional[str] = None
    consumer: Optional[str] = None
    detail: str


class SystemLintData(BaseModel):
    arch_violations: list[str] = []
    drift_warnings: list[str] = []
    event_contract_violations: list[LintFinding] = []


class SystemLintResponse(BaseModel):
    success: bool
    data: Optional[SystemLintData] = None
    error: Optional[str] = None


class EventContractAnalyzer:
    """
    Static (AST) cross-check of event bus contracts: the payload keys each
    publish site emits vs the keys each subscriber's handler consumes.

    Feed plugin sources with add_source(), then call check() for findings.
    Everything it cannot resolve statically is reported as info, never as a
    warning — the linter must not produce false alarms.
    """

    def __init__(self):
        self.publishers: list[dict] = []
        self.consumers: list[dict] = []
        self.extraction_infos: list[dict] = []

    # ---------- extraction ----------

    def add_source(self, domain: str, filename: str, source: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            self.extraction_infos.append(self._finding(
                "LINT_ERROR", "info",
                detail=f"Could not parse {domain}/{filename}: {e}"
            ))
            return

        models = self._collect_pydantic_models(tree)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self._scan_plugin_class(domain, filename, node, models)

    def _collect_pydantic_models(self, tree: ast.Module) -> dict:
        """Map of module-level BaseModel subclasses -> required/optional field names."""
        models: dict[str, dict] = {}
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = {b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", None)
                          for b in node.bases}
            if "BaseModel" not in base_names:
                continue
            required, optional = set(), set()
            for stmt in node.body:
                if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                    continue
                field = stmt.target.id
                if stmt.value is None:
                    required.add(field)
                elif (isinstance(stmt.value, ast.Call)
                      and getattr(stmt.value.func, "id", getattr(stmt.value.func, "attr", None)) == "Field"):
                    has_default = bool(stmt.value.args) or any(
                        kw.arg in ("default", "default_factory") for kw in stmt.value.keywords
                    )
                    (optional if has_default else required).add(field)
                else:
                    optional.add(field)  # plain default value
            models[node.name] = {"required": required, "optional": optional}
        return models

    def _scan_plugin_class(self, domain, filename, classdef, models) -> None:
        methods = {m.name: m for m in classdef.body
                   if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for method in methods.values():
            for call in ast.walk(method):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                if call.func.attr in ("publish", "request"):
                    self._extract_publish(domain, filename, classdef.name, method, call)
                elif call.func.attr == "subscribe":
                    self._extract_subscribe(domain, filename, classdef.name, methods, models, call)

    def _extract_publish(self, domain, filename, cls, method, call) -> None:
        if not call.args:
            return
        site = f"{domain}.{cls}.{method.name} ({filename}:{call.lineno})"
        event = self._literal_str(call.args[0])
        if event is None:
            self.extraction_infos.append(self._finding(
                "DYNAMIC_EVENT", "info", publisher=site,
                detail="publish/request with a non-literal event name — not verifiable"
            ))
            return
        if self._excluded(event):
            return

        payload_node = call.args[1] if len(call.args) > 1 else None
        keys, is_open = self._resolve_payload(payload_node, method)
        if keys is None:
            self.extraction_infos.append(self._finding(
                "UNKNOWN_PAYLOAD", "info", event=event, publisher=site,
                detail="payload is not a statically analyzable dict literal"
            ))
        self.publishers.append({
            "event": event, "site": site, "keys": keys, "open": is_open,
        })

    def _resolve_payload(self, node, method):
        """Returns (keys | None, open). None keys = not analyzable."""
        if isinstance(node, ast.Dict):
            keys, is_open = set(), False
            for k in node.keys:
                if k is None:  # **spread
                    is_open = True
                elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
                else:
                    is_open = True
            return keys, is_open
        if isinstance(node, ast.Name):
            # Trivial back-resolution: a single dict-literal assignment in the method.
            assigns = [
                stmt.value for stmt in ast.walk(method)
                if isinstance(stmt, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == node.id for t in stmt.targets)
            ]
            if len(assigns) == 1 and isinstance(assigns[0], ast.Dict):
                return self._resolve_payload(assigns[0], method)
        return None, True

    def _extract_subscribe(self, domain, filename, cls, methods, models, call) -> None:
        if len(call.args) < 2:
            return
        site = f"{domain}.{cls} ({filename}:{call.lineno})"
        event = self._literal_str(call.args[0])
        if event is None:
            self.extraction_infos.append(self._finding(
                "DYNAMIC_EVENT", "info", consumer=site,
                detail="subscribe with a non-literal event name — not verifiable"
            ))
            return
        if self._excluded(event):
            return

        handler_node = call.args[1]
        handler = None
        if (isinstance(handler_node, ast.Attribute)
                and isinstance(handler_node.value, ast.Name)
                and handler_node.value.id == "self"):
            handler = methods.get(handler_node.attr)

        consumer_name = f"{domain}.{cls}.{getattr(handler, 'name', '?')}"
        if handler is None:
            self.consumers.append({"event": event, "consumer": consumer_name,
                                   "required": set(), "optional": set(), "opaque": True})
            self.extraction_infos.append(self._finding(
                "OPAQUE_CONSUMER", "info", event=event, consumer=consumer_name,
                detail="handler could not be resolved statically"
            ))
            return

        required, optional, opaque = self._analyze_handler(handler, models)
        self.consumers.append({"event": event, "consumer": consumer_name,
                               "required": required, "optional": optional, "opaque": opaque})
        if opaque:
            self.extraction_infos.append(self._finding(
                "OPAQUE_CONSUMER", "info", event=event, consumer=consumer_name,
                detail="handler uses the payload in ways the linter cannot analyze"
            ))

    def _analyze_handler(self, handler, models):
        """Returns (required_keys, optional_keys, opaque)."""
        params = handler.args.args
        if len(params) < 2:
            return set(), set(), False
        ev = params[1].arg

        def is_payload_attr(n):
            return (isinstance(n, ast.Attribute) and n.attr == "payload"
                    and isinstance(n.value, ast.Name) and n.value.id == ev)

        aliases = {
            stmt.targets[0].id
            for stmt in ast.walk(handler)
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name) and is_payload_attr(stmt.value)
        }

        def is_payload_ref(n):
            return is_payload_attr(n) or (isinstance(n, ast.Name) and n.id in aliases)

        required, optional = set(), set()
        recognized = set()  # ids of payload-ref nodes consumed by a known pattern
        touched = False

        for node in ast.walk(handler):
            if isinstance(node, ast.Subscript) and is_payload_ref(node.value):
                key = self._literal_str(node.slice)
                if key is not None:
                    required.add(key)
                    recognized.add(id(node.value))
            elif isinstance(node, ast.Call):
                # payload.get("k") / alias.get("k"[, default]) -> optional
                if (isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                        and is_payload_ref(node.func.value) and node.args):
                    key = self._literal_str(node.args[0])
                    if key is not None:
                        optional.add(key)
                        recognized.add(id(node.func.value))
                # Model(**payload) -> required/optional from the Pydantic model
                elif isinstance(node.func, ast.Name) and node.func.id in models:
                    for kw in node.keywords:
                        if kw.arg is None and is_payload_ref(kw.value):
                            required |= models[node.func.id]["required"]
                            optional |= models[node.func.id]["optional"]
                            recognized.add(id(kw.value))
            if is_payload_ref(node) and id(node) not in recognized:
                touched = True

        # Alias assignments themselves reference the payload; don't count them.
        opaque = touched and not (required or optional)
        return required, optional, opaque

    # ---------- checking ----------

    def check(self) -> list[dict]:
        findings = list(self.extraction_infos)

        events = {p["event"] for p in self.publishers} | {c["event"] for c in self.consumers}
        for event in sorted(events):
            pubs = [p for p in self.publishers if p["event"] == event]
            cons = [c for c in self.consumers if c["event"] == event]

            if pubs and not cons:
                findings.append(self._finding(
                    "ORPHAN_PUBLISH", "info", event=event,
                    publisher=", ".join(p["site"] for p in pubs),
                    detail="event is published but has no static subscribers"
                ))
            if cons and not pubs:
                findings.append(self._finding(
                    "ORPHAN_SUBSCRIBE", "info", event=event,
                    consumer=", ".join(c["consumer"] for c in cons),
                    detail="event is subscribed to but never published statically"
                ))

            for consumer in cons:
                for key in sorted(consumer["required"]):
                    for pub in pubs:
                        if pub["keys"] is None or pub["open"]:
                            continue
                        if key not in pub["keys"]:
                            findings.append(self._finding(
                                "MISSING_KEY", "warning", event=event,
                                publisher=pub["site"], consumer=consumer["consumer"],
                                detail=(f"consumer requires key '{key}' but this publish "
                                        f"site only sends {sorted(pub['keys'])}")
                            ))

        findings.sort(key=lambda f: (f["severity"] != "warning", f["code"], f.get("event") or ""))
        return findings

    # ---------- helpers ----------

    @staticmethod
    def _literal_str(node) -> Optional[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    @staticmethod
    def _excluded(event: str) -> bool:
        return event.startswith(EXCLUDED_PREFIXES) or "*" in event

    @staticmethod
    def _finding(code, severity, event=None, publisher=None, consumer=None, detail="") -> dict:
        return LintFinding(code=code, severity=severity, event=event,
                           publisher=publisher, consumer=consumer, detail=detail).model_dump()


class EventContractLinterPlugin(BasePlugin):
    """
    Verifies event bus payload contracts at boot: every key a subscriber's
    handler requires must be present in every statically known publish site
    for that event. Companion to ArchitectureLinterPlugin.

    Also exposes GET /system/lint aggregating all linter findings
    (arch_violations, drift_warnings, event_contract_violations).
    """

    def __init__(self, container, logger, http):
        self.container = container
        self.registry = container.registry
        self.logger = logger
        self.http = http

    async def on_boot(self):
        findings = self._run_scan()
        self.registry.register_domain_metadata(
            "system", "event_contract_violations", findings
        )

        warnings = [f for f in findings if f["severity"] == "warning"]
        infos = [f for f in findings if f["severity"] != "warning"]
        for w in warnings:
            self.logger.warning(f"[EventLinter] {w['code']} {w['event']}: {w['detail']}")
        if not warnings:
            self.logger.info(
                f"[EventLinter] Event contracts verified. "
                f"No incompatibilities found ({len(infos)} informational)."
            )

        self.http.add_endpoint(
            "/api/system/lint", "GET", self.get_lint,
            tags=["System"],
            response_model=SystemLintResponse,
        )

    def _run_scan(self, domains_dir: str = "domains") -> list[dict]:
        analyzer = EventContractAnalyzer()
        base = os.path.abspath(domains_dir)
        if not os.path.exists(base):
            return []
        for domain in sorted(os.listdir(base)):
            plugins_dir = os.path.join(base, domain, "plugins")
            if not os.path.isdir(plugins_dir):
                continue
            for filename in sorted(os.listdir(plugins_dir)):
                if not filename.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(plugins_dir, filename), "r", encoding="utf-8") as f:
                        analyzer.add_source(domain, filename, f.read())
                except Exception as e:
                    self.logger.warning(f"[EventLinter] Could not read {filename}: {e}")
        return analyzer.check()

    async def get_lint(self, data: dict, context=None):
        try:
            meta = self.registry.get_domain_metadata().get("system", {})
            payload = SystemLintData(
                arch_violations=meta.get("arch_violations", []),
                drift_warnings=meta.get("drift_warnings", []),
                event_contract_violations=meta.get("event_contract_violations", []),
            )
            return {"success": True, "data": payload.model_dump()}
        except Exception as e:
            self.logger.error(f"[EventLinter] Failed to read lint metadata: {e}")
            return {"success": False, "error": "Could not retrieve lint results"}

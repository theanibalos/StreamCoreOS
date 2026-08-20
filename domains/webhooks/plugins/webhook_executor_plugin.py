import json
import asyncio
import httpx
from core.base_plugin import BasePlugin

class WebhookExecutorPlugin(BasePlugin):
    """
    Listens for commands and events to trigger registered webhooks.
    """
    def __init__(self, db, event_bus, logger):
        self.db = db
        self.bus = event_bus
        self.logger = logger
        self._http_client = httpx.AsyncClient(timeout=10.0)

    async def on_boot(self):
        await self.bus.subscribe("chat.command.executed", self._on_command)
        self.bus.add_listener(self._on_event)

    async def _on_command(self, event):
        data = event.payload
        command_name = data.get("command", "").lower()
        webhooks = await self.db.query(
            "SELECT * FROM webhooks WHERE trigger_type='command' AND trigger_value=$1 AND enabled=1",
            [command_name]
        )
        for wh in webhooks:
            asyncio.create_task(self._evaluate_and_execute(wh, data))

    async def _on_event(self, record: dict):
        # add_listener() is the bus-wide observation sink and hands over the
        # raw published record (a dict), not an EventEnvelope.
        data = record.get("payload", {})
        event_topic = record.get("event") or data.get("_event_type") or data.get("event_type")
        if not event_topic:
            return

        webhooks = await self.db.query(
            "SELECT * FROM webhooks WHERE trigger_type='event' AND trigger_value=$1 AND enabled=1",
            [event_topic]
        )
        for wh in webhooks:
            asyncio.create_task(self._evaluate_and_execute(wh, data))

    async def _evaluate_and_execute(self, webhook: dict, context_data: dict):
        # Enrich context data
        enriched_data = {**context_data}
        if "reward" in context_data and isinstance(context_data["reward"], dict):
            enriched_data["reward_title"] = context_data["reward"].get("title", "")
            enriched_data["reward_id"] = context_data["reward"].get("id", "")

        # CHECK FILTER (Option A)
        filter_field = webhook.get("filter_field")
        filter_value = webhook.get("filter_value")
        
        if filter_field and filter_value:
            actual_value = str(enriched_data.get(filter_field, ""))
            if actual_value.lower() != str(filter_value).lower():
                # Filter doesn't match, skip execution
                return

        await self._execute_webhook(webhook, enriched_data)

    async def _execute_webhook(self, webhook: dict, context_data: dict):
        url = webhook["url"]
        method = webhook["method"].upper()
        headers = json.loads(webhook["headers"]) if webhook["headers"] else {}
        body_template = webhook["body_template"]

        body = body_template
        if body:
            for key, value in context_data.items():
                placeholder = "{" + str(key) + "}"
                if placeholder in body:
                    body = body.replace(placeholder, str(value))
            
            try:
                body = json.loads(body)
            except:
                pass

        try:
            self.logger.info(f"[Webhook] Executing '{webhook['name']}' -> {url}")
            if method == "POST":
                if isinstance(body, dict):
                    await self._http_client.post(url, json=body, headers=headers)
                else:
                    await self._http_client.post(url, content=body, headers=headers)
            elif method == "GET":
                await self._http_client.get(url, headers=headers)
        except Exception as e:
            self.logger.error(f"[Webhook] Failed to execute '{webhook['name']}': {e}")

    async def shutdown(self):
        await self._http_client.aclose()

"""
Twitch EventSub WebSocket client.

Internal module — used only by TwitchTool.

Manages a persistent WebSocket connection to Twitch's EventSub service
(wss://eventsub.wss.twitch.tv/ws). Handles the full lifecycle:
  1. Connect → receive session_welcome → get session_id
  2. Create all registered subscriptions via Helix API
  3. Dispatch incoming notifications to registered callbacks
  4. Handle session_reconnect (connect to new URL before closing old one)
  5. Auto-reconnect on unexpected disconnect (exponential backoff)
"""

import asyncio
import json
from collections import defaultdict, OrderedDict

import websockets

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws"

# Twitch EventSub over WebSocket is at-least-once delivery — reconnects can
# redeliver a notification already processed. Twitch's own guidance is to
# dedupe by metadata.message_id; we only need to remember recent ids, not
# all of them forever.
_SEEN_MESSAGE_IDS_MAXLEN = 1000


class TwitchEventSubClient:

    def __init__(self, api, client_id: str) -> None:
        self._api = api
        self._client_id = client_id
        # event_type → list of async/sync callbacks
        self._callbacks: dict[str, list] = defaultdict(list)
        # subscriptions registered by plugins before connect()
        self._subscriptions: list[dict] = []
        self._access_token: str | None = None
        self._broadcaster_id: str | None = None
        self._session_id: str | None = None
        self._task: asyncio.Task | None = None
        self._connected = False
        self._seen_message_ids: "OrderedDict[str, None]" = OrderedDict()

    # ── Registration API ─────────────────────────────────────────────

    def on_event(self, event_type: str, callback) -> None:
        """Register a callback for a specific event type. Use '*' for all events."""
        self._callbacks[event_type].append(callback)

    def register_subscription(
        self, event_type: str, version: str, condition: dict
    ) -> None:
        """Store a subscription to be created after connect()."""
        self._subscriptions.append(
            {"type": event_type, "version": version, "condition": condition}
        )

    # ── Connection API ────────────────────────────────────────────────

    async def connect(self, access_token: str, broadcaster_id: str) -> None:
        self._access_token = access_token
        self._broadcaster_id = broadcaster_id
        if self._task:
            self._task.cancel()
        self._task = asyncio.create_task(self._run(EVENTSUB_WS_URL))

    async def disconnect(self) -> None:
        self._connected = False
        if self._task:
            self._task.cancel()
            self._task = None

    # ── Internal WebSocket loop ───────────────────────────────────────

    async def _run(self, initial_url: str) -> None:
        url = initial_url
        is_reconnect = False
        retry_delay = 1
        
        while True:
            try:
                async with websockets.connect(url) as ws:
                    retry_delay = 1
                    async for raw in ws:
                        msg = json.loads(raw)
                        msg_type = msg.get("metadata", {}).get("message_type")

                        if msg_type == "session_welcome":
                            self._session_id = msg["payload"]["session"]["id"]
                            self._connected = True
                            print(f"[TwitchEventSub] Connected — session_id={self._session_id}")
                            
                            if is_reconnect:
                                print("[TwitchEventSub] Reconnected gracefully (subscriptions preserved by Twitch).")
                                is_reconnect = False
                            else:
                                await self._create_subscriptions()

                        elif msg_type == "session_keepalive":
                            pass

                        elif msg_type == "notification":
                            message_id = msg.get("metadata", {}).get("message_id")
                            await self._dispatch(msg["payload"], message_id)

                        elif msg_type == "session_reconnect":
                            new_url = msg["payload"]["session"]["reconnect_url"]
                            print(f"[TwitchEventSub] Reconnecting to {new_url}")
                            url = new_url
                            is_reconnect = True
                            # Breaking the async for closes the current WS and continues the while loop
                            break

                        elif msg_type == "revocation":
                            sub_type = msg["payload"]["subscription"]["type"]
                            reason = msg["payload"]["subscription"]["status"]
                            print(f"[TwitchEventSub] Subscription revoked: {sub_type} ({reason})")

                    # If we reached here without a break (e.g. WS closed normally), 
                    # we should probably fall back to the default URL
                    if not is_reconnect:
                        url = EVENTSUB_WS_URL

            except asyncio.CancelledError:
                return
            except Exception as e:
                self._connected = False
                is_reconnect = False
                print(f"[TwitchEventSub] Connection error: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)
                url = EVENTSUB_WS_URL

    async def _create_subscriptions(self) -> None:
        for sub in self._subscriptions:
            condition = {
                k: v.replace("{broadcaster_id}", self._broadcaster_id)
                if isinstance(v, str) else v
                for k, v in sub["condition"].items()
            }
            try:
                await self._api.post(
                    "/eventsub/subscriptions",
                    body={
                        "type": sub["type"],
                        "version": sub["version"],
                        "condition": condition,
                        "transport": {
                            "method": "websocket",
                            "session_id": self._session_id,
                        },
                    },
                    user_token=self._access_token,
                )
                print(f"[TwitchEventSub] Subscribed to {sub['type']}")
            except Exception as e:
                print(f"[TwitchEventSub] Failed to subscribe to {sub['type']}: {e}")

    async def _dispatch(self, payload: dict, message_id: str | None = None) -> None:
        if message_id:
            if message_id in self._seen_message_ids:
                print(f"[TwitchEventSub] Duplicate notification {message_id} — skipping redelivery.")
                return
            self._seen_message_ids[message_id] = None
            if len(self._seen_message_ids) > _SEEN_MESSAGE_IDS_MAXLEN:
                self._seen_message_ids.popitem(last=False)

        sub_type = payload.get("subscription", {}).get("type", "")
        event_data = payload.get("event", {})

        # Specific callbacks receive just the event payload
        for callback in self._callbacks.get(sub_type, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event_data)
                else:
                    callback(event_data)
            except Exception as e:
                print(f"[TwitchEventSub] Error in callback for {sub_type}: {e}")

        # Wildcard callbacks receive event_data enriched with _event_type
        if self._callbacks.get("*"):
            enriched = {"_event_type": sub_type, **event_data}
            for callback in self._callbacks["*"]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(enriched)
                    else:
                        callback(enriched)
                except Exception as e:
                    print(f"[TwitchEventSub] Error in wildcard callback for {sub_type}: {e}")

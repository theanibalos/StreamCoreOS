import secrets
from typing import Optional, Any
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin

# Sample payloads per contract type. Fired from the dashboard "test" button so an
# overlay author can see their overlay react without waiting for a real follow.
# Published on the bus as "overlay.test.event"; the feed plugin re-broadcasts it
# with `test: true`. Plugins compose through the bus — never import each other.
_SAMPLES = {
    "event.follow":       {"user": "TestFollower", "user_id": "0"},
    "event.subscription": {"user": "TestSub", "user_id": "0", "tier": "1000", "months": 3, "message": "¡Sub de prueba!"},
    "event.raid":         {"user": "TestRaider", "user_id": "0", "viewers": 42},
    "event.cheer":        {"user": "TestCheerer", "user_id": "0", "bits": 500, "message": "¡Bits de prueba!"},
    "event.redemption":   {
        "user": "TestRedeemer", "user_id": "0",
        "reward_name": "Hidratación", "cost": "250", "user_input": "¡Bebe agua!"
    },
    "event.superchat": {"platform": "youtube", "user": "TestSupporter", "user_id": "0", "amount_micros": 5000000, "currency": "USD", "display_amount": "$5.00", "message": "Hola"},
    "event.supersticker": {"platform": "youtube", "user": "TestSupporter", "user_id": "0", "amount_micros": 2000000, "currency": "USD", "display_amount": "$2.00", "message": ""},
    "event.member": {"platform": "youtube", "user": "TestMember", "user_id": "0", "message": "Nuevo miembro"},
    "chat.message":       {
        "user": "TestChatter", "user_id": "0", "color": "#a970ff",
        "badges": [{"set": "moderator", "version": "1", "url": ""}],
        "text": "Mensaje de prueba Kappa 👋",
        "fragments": [
            {"type": "text", "text": "Mensaje de prueba "},
            {"type": "emote", "name": "Kappa", "emote_id": "25", "emote_animated": False,
             "url": "https://static-cdn.jtvnw.net/emoticons/v2/25/static/dark/2.0"},
            {"type": "text", "text": " 👋"},
        ],
    },
}


class TestEventRequest(BaseModel):
    type: str = Field(min_length=1)
    reward_name: Optional[str] = Field(default=None, min_length=1, max_length=100)


class TestResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class OverlayTestPlugin(BasePlugin):
    """
    POST /api/overlays/test   { "type": "event.follow" | ... }

    Injects a fake event into the overlay feed so an author can preview their
    overlay live. Guarded by the active Twitch session (same model as the token
    endpoints). Publishes "overlay.test.event"; the feed marks it `test: true`.
    """

    def __init__(self, http, event_bus, twitch, logger):
        self.http = http
        self.bus = event_bus
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/test", "POST", self.execute,
            tags=["Overlays"],
            request_model=TestEventRequest,
            response_model=TestResponse,
        )

    async def execute(self, data: dict, context=None):
        if self.twitch.get_session() is None:
            return {"success": False, "error": "Not authenticated"}
        try:
            req = TestEventRequest(**data)
        except Exception:
            return {"success": False, "error": "Invalid request"}

        sample = _SAMPLES.get(req.type)
        if sample is None:
            return {"success": False, "error": f"Unknown test type: {req.type}"}

        payload = {"id": f"test-{secrets.token_hex(4)}", **sample}
        if req.type == "event.redemption" and req.reward_name:
            payload["reward_name"] = req.reward_name
        await self.bus.publish("overlay.test.event", {"type": req.type, "data": payload})
        return {"success": True, "data": {"type": req.type}}

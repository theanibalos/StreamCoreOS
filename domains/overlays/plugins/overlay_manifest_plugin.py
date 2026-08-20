from typing import Optional, Any
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ManifestResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class OverlayManifestPlugin(BasePlugin):
    """
    GET /api/overlays/manifest   (public)

    The machine- and AI-legible description of the overlay feed contract: every
    message `type`, its fields, and the known `stat.update` keys. This is the
    "manual" — paste it into an AI and ask it to build an overlay that consumes
    /api/overlays/feed. Because it is served from the code it can never drift
    from what the feed actually emits. Contract: OVERLAY_FEED_CONTRACT.md.
    """

    def __init__(self, http, logger):
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/manifest", "GET", self.execute,
            tags=["Overlays"],
            response_model=ManifestResponse,
        )

    async def execute(self, data: dict, context=None):
        manifest = {
            "contract": "overlay-feed",
            "version": 1,
            "transport": {
                "endpoint": "/api/overlays/feed?token=<channel_overlay_token>",
                "protocol": "SSE",
                "envelope": {"type": "string", "v": "int", "ts": "epoch_ms", "data": "object"},
                "note": "Switch on `type`. Ignore unknown types. Default missing fields (tolerant reader).",
            },
            "types": {
                "feed.snapshot": {
                    "when": "first event on connect",
                    "data": {"stats": "object (see stat keys)", "recent_chat": "chat.message data[]", "active": "array"},
                },
                "stat.update": {
                    "when": "a counter changed",
                    "data": {"key": "string", "value": "number", "previous": "number|null", "display": "string"},
                    "known_keys": ["followers", "subs", "viewers", "bits"],
                },
                "event.follow":            {"data": {"id": "string", "user": "string", "user_id": "string"}},
                "event.subscription":      {"data": {"id": "string", "user": "string", "user_id": "string", "tier": "string", "months": "int", "message": "string"}},
                "event.subscription.gift": {"data": {"id": "string", "user": "string", "user_id": "string", "tier": "string"}},
                "event.raid":              {"data": {"id": "string", "user": "string", "user_id": "string", "viewers": "int"}},
                "event.cheer":             {"data": {"id": "string", "user": "string", "user_id": "string", "bits": "int", "message": "string"}},
                "chat.message": {
                    "data": {
                        "id": "string", "user": "string", "user_id": "string", "color": "hex string",
                        "badges": "[{set, version, url}]",
                        "text": "plain string",
                        "fragments": "[{type:'text',text} | {type:'emote',name,emote_id,emote_animated,url}]",
                    },
                },
            },
            "rules": [
                "Additive only: types and fields are never removed or renamed.",
                "New capability = new type namespace or new stat key.",
                "Consumers are tolerant readers: ignore unknown types, default missing fields.",
                "Emote and badge image URLs are pre-resolved server-side — just drop them into <img src>.",
                "Events fired from the dashboard 'test' button carry data.test = true; render them like real ones.",
            ],
        }
        return {"success": True, "data": manifest}

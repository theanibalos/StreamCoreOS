from typing import Optional, Any
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


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
    /api/overlays/feed. Contract: OVERLAY_FEED_CONTRACT.md.
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
                    "data": {"key": "string", "value": "JSON value", "previous": "JSON value|null", "display": "string"},
                    "known_keys": ["followers", "subs", "viewers", "bits"],
                },
                "event.follow":            {"data": {"id": "string", "user": "string", "user_id": "string"}},
                "event.subscription":      {"data": {"id": "string", "user": "string", "user_id": "string", "tier": "string", "months": "int", "message": "string"}},
                "event.subscription.gift": {"data": {"id": "string", "user": "string", "user_id": "string", "tier": "string", "total": "int"}},
                "event.raid":              {"data": {"id": "string", "user": "string", "user_id": "string", "viewers": "int"}},
                "event.cheer":             {"data": {"id": "string", "user": "string", "user_id": "string", "bits": "int", "message": "string"}},
                "event.redemption":        {"data": {"id": "string", "user": "string", "user_id": "string", "reward_name": "string", "cost": "int", "user_input": "string"}},
                "event.twitch":            {"data": {"id": "string", "event_type": "raw Twitch EventSub type", "user": "string", "user_id": "string", "details": "EventSub payload"}},
                "event.superchat":         {"data": {"id": "string", "platform": "youtube", "user": "string", "user_id": "string", "amount_micros": "int", "currency": "string", "display_amount": "string", "message": "string"}},
                "event.supersticker":      {"data": {"id": "string", "platform": "youtube", "user": "string", "user_id": "string", "amount_micros": "int", "currency": "string", "display_amount": "string", "message": "string"}},
                "event.member":           {"data": {"id": "string", "platform": "youtube", "user": "string", "user_id": "string", "message": "string"}},
                "chat.delete":            {"data": {"id": "string", "platform": "string", "channel_id": "string"}},
                "stream.session.started": {"data": {"session_id": "int", "started_at": "ISO timestamp", "broadcaster_login": "string"}},
                "stream.session.ended": {"data": {"session_id": "int", "ended_at": "ISO timestamp"}},
                "viewer.regular.added": {"data": {"global_user_id": "string", "platform": "string", "display_name": "string"}},
                "viewer.regular.removed": {"data": {"global_user_id": "string", "platform": "string", "display_name": "string"}},
                "moderation.action.taken": {"data": {"platform": "string", "user": "string", "user_id": "string", "action": "string", "reason": "string"}},
                "chat.command.received": {"data": {"command": "string", "args": "string", "user": "string", "user_id": "string"}},
                "feed.error": {"data": {"error": "string"}},
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
                "Use fragments[].url for emote images and badges[].url for badge images; preserve fragment order.",
                "A badge URL may be empty (for example on YouTube); show its set as a text label.",
                "Events fired from the dashboard 'test' button carry data.test = true; render them like real ones.",
            ],
        }
        return {"success": True, "data": manifest}

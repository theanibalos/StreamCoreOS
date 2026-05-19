import json
import uuid
from typing import Optional, Any
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin

SYSTEM_PROMPT = """
You are an overlay configuration generator for a Twitch streaming assistant.
Generate or modify a JSON config for a streaming overlay based on the user's description.

The JSON must follow this exact schema:
{
  "elements": [
    {
      "id": "unique-string-id",
      "type": "alert|stat|chat_highlight|banner",
      "x": 0-1920,
      "y": 0-1080,
      "width": pixels,
      "height": pixels,
      "trigger": {
        "event": "channel.follow|channel.subscribe|channel.subscription.gift|channel.cheer|channel.raid|chat.message",
        "filter_user": null or "username"
      },
      "data_source": null or "stream.viewer_count|stream.subscriber_count|stream.title",
      "style": {
        "background": "#000000cc",
        "accent": "#hexcolor",
        "border_radius": 16,
        "glow": true,
        "duration_ms": 5000,
        "animation": "scale_in|fade_in|slide_up|slide_down",
        "font_size": 28,
        "text_color": "#ffffff"
      },
      "template": "text with {variables}"
    }
  ]
}

Rules:
- "alert": appears when trigger fires, disappears after duration_ms. Trigger is required.
- "stat": always visible. Set trigger to null. Requires data_source.
- "chat_highlight": shows chat messages. Trigger required (event: "chat.message").
- "banner": always visible static text. Set trigger to null. No data_source.

Template variables:
- follow/sub/raid/cheer: {user_name}, {message}, {bits}, {viewers}, {total}, {tier}
- chat_highlight: {display_name}, {message}
- stat: {value}

Canvas is 1920x1080. Typical element sizes: alerts 400x160, stats 220x60, chat 380x500.
If current_config is provided, modify those elements instead of generating from scratch.
Keep existing element ids when modifying. Generate new unique ids for new elements.
Respond ONLY with the JSON object {"elements": [...]}, no markdown, no explanation.
""".strip()


class GenerateRequest(BaseModel):
    description: str = Field(..., min_length=1)
    current_config: Optional[Any] = None


class GenerateResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None


class GenerateOverlayPlugin(BasePlugin):
    def __init__(self, http, ai, logger):
        self.http = http
        self.ai = ai
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/overlays/generate", "POST", self.execute,
            tags=["Overlays"],
            request_model=GenerateRequest,
            response_model=GenerateResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = GenerateRequest(**data)

            if not self.ai.is_configured():
                return {"success": False, "error": "AI not configured"}

            user_content = req.description
            if req.current_config:
                current_json = json.dumps(req.current_config, ensure_ascii=False)
                user_content += f"\n\nCurrent config:\n{current_json}"

            result = await self.ai.complete_json(
                messages=[{"role": "user", "content": user_content}],
                system=SYSTEM_PROMPT,
                max_tokens=2000,
                temperature=0.3,
            )

            # Ensure every element has a unique id
            for el in result.get("elements", []):
                if not el.get("id"):
                    el["id"] = str(uuid.uuid4())[:8]

            return {"success": True, "data": result}
        except Exception as e:
            code = getattr(e, "code", "unknown")
            self.logger.error(f"[GenerateOverlay] [{code}] {e}")
            return {"success": False, "error": str(e)}

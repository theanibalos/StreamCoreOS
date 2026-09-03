import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin


class YouTubeOAuthCallbackResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class YouTubeOAuthCallbackPlugin(BasePlugin):
    def __init__(self, youtube, http, db, event_bus, logger, config):
        self.youtube = youtube
        self.http = http
        self.db = db
        self.bus = event_bus
        self.logger = logger
        self.config = config

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/auth/youtube/callback", "GET", self.execute,
            tags=["YouTube Auth"], response_model=YouTubeOAuthCallbackResponse,
        )

    async def execute(self, data: dict, context=None):
        code = data.get("code")
        received_state = data.get("state")
        error = data.get("error")
        if error:
            return {"success": False, "error": f"YouTube denied access: {error}"}
        if not code:
            return {"success": False, "error": "Missing code parameter"}
        if not received_state or not self.youtube.consume_state(received_state):
            return {"success": False, "error": "Invalid or expired state"}

        try:
            tokens = await self.youtube.exchange_code(code)
            access_token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token")
            expires_in = int(tokens.get("expires_in", 3600))
            scopes = tokens.get("scope", "").split()
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

            await self.youtube.connect(access_token, refresh_token, "pending", "pending", expires_in)
            info = await self.youtube.get_user_info()
            channel_id = info["id"]
            channel_title = info["title"]
            await self.youtube.connect(access_token, refresh_token, channel_id, channel_title, expires_in)

            existing = await self.db.query_one("SELECT id, refresh_token FROM youtube_tokens WHERE channel_id=$1", [channel_id])
            stored_refresh = refresh_token or (existing or {}).get("refresh_token")
            if existing:
                await self.db.execute(
                    """UPDATE youtube_tokens
                       SET channel_title=$1, access_token=$2, refresh_token=$3, scopes=$4,
                           expires_at=$5, updated_at=datetime('now')
                       WHERE channel_id=$6""",
                    [channel_title, access_token, stored_refresh, json.dumps(scopes), expires_at, channel_id],
                )
            else:
                await self.db.execute(
                    """INSERT INTO youtube_tokens
                       (channel_id, channel_title, access_token, refresh_token, scopes, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    [channel_id, channel_title, access_token, stored_refresh, json.dumps(scopes), expires_at],
                )

            capabilities = {
                "chat.read": True,
                "chat.write": True,
                "moderation.delete": True,
                "moderation.timeout": True,
                "moderation.ban": True,
                "events.subscription": False,
                "events.cheer": False,
                "events.superchat": True,
                "stream.status": True,
            }
            connection_id = await self.db.execute(
                """INSERT INTO platform_connections (
                       platform, channel_id, channel_name, enabled,
                       chat_read_enabled, chat_write_enabled, moderation_enabled, capabilities
                   )
                   VALUES ('youtube', $1, $2, 1, 1, 1, 1, $3)
                   ON CONFLICT(platform, channel_id) DO UPDATE SET
                       channel_name = excluded.channel_name,
                       enabled = 1,
                       chat_read_enabled = 1,
                       chat_write_enabled = 1,
                       moderation_enabled = 1,
                       capabilities = excluded.capabilities,
                       updated_at = datetime('now')
                   RETURNING id""",
                [channel_id, channel_title, json.dumps(capabilities)],
            )
            await self.bus.publish("platform.connection.updated", {
                "id": connection_id,
                "platform": "youtube",
                "channel_id": channel_id,
                "channel_name": channel_title,
                "enabled": True,
                "chat_read_enabled": True,
                "chat_write_enabled": True,
                "moderation_enabled": True,
                "capabilities": capabilities,
            })

            self.logger.info(f"[YouTubeAuth] Connected as {channel_title} ({channel_id})")
            frontend_url = self.config.get("FRONTEND_URL", "/")
            context.redirect(frontend_url)
            return {}
        except Exception as e:
            self.logger.error(f"[YouTubeOAuthCallback] {e}")
            return {"success": False, "error": str(e)}

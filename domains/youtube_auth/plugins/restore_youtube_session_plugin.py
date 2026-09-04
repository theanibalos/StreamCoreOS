import json
from datetime import datetime, timezone
from microcoreos.base_plugin import BasePlugin


class RestoreYouTubeSessionPlugin(BasePlugin):
    def __init__(self, youtube, db, logger):
        self.youtube = youtube
        self.db = db
        self.logger = logger

    async def on_boot(self):
        try:
            token = await self.db.query_one(
                "SELECT channel_id, channel_title, access_token, refresh_token, expires_at FROM youtube_tokens LIMIT 1", []
            )
            if not token:
                self.logger.info("[RestoreYouTubeSession] No stored YouTube token found. Awaiting OAuth.")
                return
            expires_in = 3600
            try:
                expires_at = datetime.fromisoformat(token["expires_at"])
                expires_in = max(60, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
            except Exception:
                pass
            await self.youtube.connect(
                token["access_token"], token["refresh_token"], token["channel_id"], token["channel_title"], expires_in
            )

            capabilities = {
                "chat.read": True,
                "chat.write": True,
                "moderation.delete": True,
                "moderation.timeout": False,
                "moderation.ban": False,
                "events.subscription": False,
                "events.cheer": False,
                "events.superchat": True,
                "stream.status": True,
            }
            await self.db.execute(
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
                       updated_at = datetime('now')""",
                [token["channel_id"], token["channel_title"], json.dumps(capabilities)],
            )

            self.logger.info(f"[RestoreYouTubeSession] Session and platform connection restored for {token['channel_title']}")
        except Exception as e:
            self.logger.error(f"[RestoreYouTubeSession] Failed to restore session: {e}")

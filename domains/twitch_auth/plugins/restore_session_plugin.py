import json
from microcoreos.base_plugin import BasePlugin


class RestoreSessionPlugin(BasePlugin):
    """
    On boot, reads the stored Twitch token from DB and reconnects the tool.

    This handles the case where the server restarts after the streamer has
    already authenticated. Without this plugin, the tool would start
    disconnected and require a manual re-authentication on every restart.
    """

    def __init__(self, twitch, db, logger):
        self.twitch = twitch
        self.db = db
        self.logger = logger

    async def on_boot(self):
        try:
            token = await self.db.query_one(
                "SELECT twitch_id, login, access_token, refresh_token FROM twitch_tokens LIMIT 1"
            )
            if not token:
                self.logger.info("[RestoreSession] No stored Twitch token found. Awaiting OAuth.")
                return

            self.logger.info(f"[RestoreSession] Restoring session for {token['login']}")
            await self.twitch.connect(
                token["access_token"], token["refresh_token"], token["twitch_id"], token["login"]
            )

            # Ensure platform_connections has the Twitch channel record
            capabilities = {
                "chat.read": True,
                "chat.write": True,
                "moderation.delete": True,
                "moderation.timeout": True,
                "moderation.ban": True,
                "events.subscription": True,
                "events.cheer": True,
                "events.superchat": False,
                "stream.status": True,
            }
            await self.db.execute(
                """INSERT INTO platform_connections (
                       platform, channel_id, channel_name, enabled,
                       chat_read_enabled, chat_write_enabled, moderation_enabled, capabilities
                   )
                   VALUES ('twitch', $1, $2, 1, 1, 1, 1, $3)
                   ON CONFLICT(platform, channel_id) DO UPDATE SET
                       channel_name = excluded.channel_name,
                       enabled = 1,
                       chat_read_enabled = 1,
                       chat_write_enabled = 1,
                       moderation_enabled = 1,
                       capabilities = excluded.capabilities,
                       updated_at = datetime('now')""",
                [token["twitch_id"], token["login"], json.dumps(capabilities)],
            )

            self.logger.info(f"[RestoreSession] Session and platform connection restored for {token['login']}")
        except Exception as e:
            self.logger.error(f"[RestoreSession] Failed to restore session: {e}")

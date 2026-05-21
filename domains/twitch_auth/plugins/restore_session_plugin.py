from core.base_plugin import BasePlugin


class RestoreSessionPlugin(BasePlugin):
    """
    On boot, reads the stored Twitch token from DB and reconnects the tool.

    This handles the case where the server restarts after the streamer has
    already authenticated. Without this plugin, the tool would start
    disconnected and require a manual re-authentication on every restart.

    If the stored access token is expired, TwitchTokenRefreshPlugin handles
    it automatically: the first 401 triggers a reactive refresh. If the
    refresh token is also expired, that plugin disconnects and clears the
    session so the frontend shows the login screen.
    """

    def __init__(self, twitch, db, logger):
        self.twitch = twitch
        self.db = db
        self.logger = logger

    async def on_boot(self):
        try:
            token = await self.db.query_one(
                "SELECT twitch_id, login, access_token FROM twitch_tokens LIMIT 1"
            )
            if not token:
                self.logger.info("[RestoreSession] No stored Twitch token found. Awaiting OAuth.")
                return

            self.logger.info(f"[RestoreSession] Restoring session for {token['login']}")
            await self.twitch.connect(
                token["access_token"], token["twitch_id"], token["login"]
            )
            self.logger.info(f"[RestoreSession] Session restored for {token['login']}")
        except Exception as e:
            self.logger.error(f"[RestoreSession] Failed to restore session: {e}")

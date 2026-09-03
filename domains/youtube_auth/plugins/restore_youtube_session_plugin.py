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
            self.logger.info(f"[RestoreYouTubeSession] Session restored for {token['channel_title']}")
        except Exception as e:
            self.logger.error(f"[RestoreYouTubeSession] Failed to restore session: {e}")

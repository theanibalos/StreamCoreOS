import json
from datetime import datetime, timedelta, timezone
from core.base_plugin import BasePlugin


class YouTubeTokenRefreshPlugin(BasePlugin):
    def __init__(self, youtube, db, logger):
        self.youtube = youtube
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.youtube.on_token_refreshed = self._on_refreshed

    async def _on_refreshed(self, tokens: dict):
        session = self.youtube.get_session()
        if not session:
            return
        expires_in = int(tokens.get("expires_in", 3600))
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        scopes = tokens.get("scope", "")
        scopes_json = json.dumps(scopes.split() if isinstance(scopes, str) else scopes)
        await self.db.execute(
            """UPDATE youtube_tokens
               SET access_token=$1, refresh_token=$2, scopes=$3, expires_at=$4, updated_at=datetime('now')
               WHERE channel_id=$5""",
            [tokens["access_token"], tokens.get("refresh_token"), scopes_json, expires_at, session["channel_id"]],
        )
        self.logger.info("[YouTubeTokenRefresh] Token refreshed and persisted")

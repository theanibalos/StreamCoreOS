import time
from core.base_plugin import BasePlugin


class ChatBadgesPlugin(BasePlugin):
    """
    GET /chat/badges

    Returns global Twitch badge image URLs as a nested dict:
      { set_id: { version: image_url_1x } }

    Fetched from Twitch /helix/chat/badges/global and cached for 1 hour.
    Falls back to an empty dict if no active session.
    """

    def __init__(self, http, twitch, logger):
        self.http = http
        self.twitch = twitch
        self.logger = logger
        self._cache: dict = {}
        self._cache_at: float = 0.0

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/chat/badges",
            "GET",
            self.execute,
            tags=["Chat"],
        )

    async def execute(self, data: dict, context=None) -> dict:
        now = time.time()
        if self._cache and now - self._cache_at < 3600:
            return self._cache

        session = self.twitch.get_session()
        if not session:
            return {}

        try:
            resp = await self.twitch.get(
                "/chat/badges/global",
                user_token=session["access_token"],
            )
            result: dict = {}
            for badge_set in resp.get("data", []):
                set_id = badge_set.get("set_id", "")
                versions: dict = {}
                for v in badge_set.get("versions", []):
                    versions[v["id"]] = v.get("image_url_1x", "")
                result[set_id] = versions

            self._cache = result
            self._cache_at = now
            return result
        except Exception as e:
            self.logger.error(f"[ChatBadges] Failed to fetch badges: {e}")
            return self._cache

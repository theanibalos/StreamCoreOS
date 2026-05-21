from core.base_plugin import BasePlugin


class SyncSubscribersPlugin(BasePlugin):
    """
    POST /subscribers/sync
    Fetches all current subscribers from Twitch API (/helix/subscriptions)
    and upserts them into the local DB. Paginates automatically.
    Requires channel:read:subscriptions scope.
    """

    def __init__(self, http, twitch, db, logger):
        self.http = http
        self.twitch = twitch
        self.db = db
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/subscribers/sync", "POST", self.execute,
            tags=["Subscribers"],
        )

    async def execute(self, data: dict, context=None):
        session = self.twitch.get_session()
        if not session:
            return {"success": False, "error": "No hay sesión de Twitch activa"}

        broadcaster_id = session["broadcaster_id"]
        access_token = session["access_token"]

        try:
            total = 0
            cursor = None

            while True:
                params = {"broadcaster_id": broadcaster_id, "first": 100}
                if cursor:
                    params["after"] = cursor

                resp = await self.twitch.get(
                    "/subscriptions", params=params, user_token=access_token
                )

                subs = resp.get("data", [])
                for sub in subs:
                    tier = sub.get("tier", "1000")
                    is_gift = 1 if sub.get("is_gift", False) else 0
                    is_prime = 1 if tier == "Prime" else 0
                    if tier == "Prime":
                        tier = "1000"

                    await self.db.execute(
                        """INSERT INTO subscribers
                               (twitch_id, login, display_name, tier, is_prime, is_gift, is_active)
                           VALUES ($1, $2, $3, $4, $5, $6, 1)
                           ON CONFLICT(twitch_id) DO UPDATE SET
                               login        = excluded.login,
                               display_name = excluded.display_name,
                               tier         = excluded.tier,
                               is_gift      = excluded.is_gift,
                               is_active    = 1""",
                        [
                            sub["user_id"],
                            sub["user_login"],
                            sub["user_name"],
                            tier,
                            is_prime,
                            is_gift,
                        ],
                    )
                    total += 1

                pagination = resp.get("pagination", {})
                cursor = pagination.get("cursor")
                if not cursor or not subs:
                    break

            self.logger.info(f"[SyncSubscribers] synced {total} subscribers")
            return {"success": True, "data": {"synced": total}}

        except Exception as e:
            self.logger.error(f"[SyncSubscribers] {e}")
            return {"success": False, "error": str(e)}

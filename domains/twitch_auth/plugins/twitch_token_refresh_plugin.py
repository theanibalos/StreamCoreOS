import asyncio
import json
from datetime import datetime, timedelta, timezone
from core.base_plugin import BasePlugin


class TwitchTokenRefreshPlugin(BasePlugin):
    """
    Scheduled job (every 30 minutes) that proactively refreshes Twitch
    access tokens expiring within the next 60 minutes. If a token is
    refreshed successfully, it updates the in-memory token without
    reconnecting EventSub.
    """

    def __init__(self, twitch, db, scheduler, logger):
        self.twitch = twitch
        self.db = db
        self.scheduler = scheduler
        self.logger = logger
        self._refresh_lock = asyncio.Lock()

    async def on_boot(self):
        self.scheduler.add_job(
            "*/30 * * * *",
            self._refresh_expiring_tokens,
            job_id="twitch_token_refresh",
        )
        self.twitch.on_token_refreshed = self._handle_token_refreshed
        self.twitch.on_auth_failed = self._handle_auth_failed

    async def _handle_token_refreshed(self, access_token: str, refresh_token: str, expires_in: int):
        """
        Called when TwitchTool successfully refreshes the session tokens autonomously.
        Persists the updated credentials to the database.
        """
        session = self.twitch.get_session()
        if not session:
            return

        self.logger.info(f"[TwitchTokenRefresh] Storing updated tokens for {session['login']}")
        try:
            token_data = await self.db.query_one(
                "SELECT scopes FROM twitch_tokens WHERE twitch_id = $1",
                [session["broadcaster_id"]]
            )
            scopes = json.loads(token_data["scopes"]) if token_data else []

            now = datetime.now(timezone.utc)
            new_expires_at = (now + timedelta(seconds=expires_in)).isoformat()

            await self.db.execute(
                """UPDATE twitch_tokens
                   SET access_token=$1, refresh_token=$2,
                       expires_at=$3, updated_at=datetime('now')
                   WHERE twitch_id=$4""",
                [access_token, refresh_token, new_expires_at, session["broadcaster_id"]],
            )
            # Update the memory state on the tool
            await self.twitch.update_access_token(access_token, refresh_token)
        except Exception as e:
            self.logger.error(f"[TwitchTokenRefresh] Failed to persist refreshed tokens: {e}")

    async def _handle_auth_failed(self):
        """
        Called when TwitchTool encounters a terminal authentication failure.
        Clears the stored session and disconnects active services.
        """
        session = self.twitch.get_session()
        if not session:
            return

        self.logger.warning(
            f"[TwitchTokenRefresh] Authentication failed terminally for {session['login']}. "
            "Clearing session."
        )
        try:
            await self.db.execute(
                "DELETE FROM twitch_tokens WHERE twitch_id = $1",
                [session["broadcaster_id"]],
            )
            await self.twitch.disconnect()
        except Exception as e:
            self.logger.error(f"[TwitchTokenRefresh] Failed to clear invalid session: {e}")

    async def _refresh_expiring_tokens(self):
        async with self._refresh_lock:
            try:
                tokens = await self.db.query("SELECT * FROM twitch_tokens")
                now = datetime.now(timezone.utc)
                threshold = now + timedelta(minutes=60)

                for token in tokens:
                    expires_at = datetime.fromisoformat(token["expires_at"])
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)

                    if expires_at > threshold:
                        continue  # still fresh, skip

                    self.logger.info(f"[TwitchTokenRefresh] Proactively refreshing token for {token['login']}")
                    try:
                        new_tokens = await self.twitch.refresh_user_token(token["refresh_token"])
                        new_access = new_tokens["access_token"]
                        new_refresh = new_tokens["refresh_token"]
                        new_expires_at = (
                            now + timedelta(seconds=new_tokens.get("expires_in", 14400))
                        ).isoformat()
                        new_scopes = new_tokens.get("scope", json.loads(token["scopes"]))

                        await self.db.execute(
                            """UPDATE twitch_tokens
                               SET access_token=$1, refresh_token=$2,
                                   scopes=$3, expires_at=$4, updated_at=datetime('now')
                               WHERE twitch_id=$5""",
                            [new_access, new_refresh, json.dumps(new_scopes),
                             new_expires_at, token["twitch_id"]],
                        )

                        # Update the in-memory token only — EventSub stays connected
                        await self.twitch.update_access_token(new_access, new_refresh)
                        self.logger.info(
                            f"[TwitchTokenRefresh] Refreshed token for {token['login']}"
                        )
                    except Exception as e:
                        status = getattr(getattr(e, "response", None), "status_code", None)
                        if status in (400, 401) or "invalid_grant" in str(e).lower():
                            self.logger.warning(
                                f"[TwitchTokenRefresh] Refresh token revoked during scheduled refresh for {token['login']}. "
                                "Clearing session — re-auth required."
                            )
                            await self.db.execute(
                                "DELETE FROM twitch_tokens WHERE twitch_id = $1",
                                [token["twitch_id"]],
                            )
                            # Only disconnect if this was the active session
                            session = self.twitch.get_session()
                            if session and session["broadcaster_id"] == token["twitch_id"]:
                                await self.twitch.disconnect()
                        else:
                            self.logger.error(
                                f"[TwitchTokenRefresh] Failed to refresh token for {token['login']}: {e}"
                            )
            except Exception as e:
                self.logger.error(f"[TwitchTokenRefresh] Unexpected error in scheduled refresh: {e}")

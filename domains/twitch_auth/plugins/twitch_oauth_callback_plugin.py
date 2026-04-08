import os
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class OAuthCallbackResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None


class TwitchOAuthCallbackPlugin(BasePlugin):
    """
    GET /auth/twitch/callback

    Receives the authorization code from Twitch, exchanges it for tokens,
    fetches the user's profile, persists the token to DB, and connects
    the EventSub WebSocket + IRC chat.
    """

    def __init__(self, twitch, http, db, event_bus, state, logger):
        self.twitch = twitch
        self.http = http
        self.db = db
        self.bus = event_bus
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/auth/twitch/callback",
            "GET",
            self.execute,
            tags=["Twitch Auth"],
            response_model=OAuthCallbackResponse,
        )

    async def execute(self, data: dict, context=None):
        code = data.get("code")
        received_state = data.get("state")
        error = data.get("error")

        if error:
            return {"success": False, "error": f"Twitch denied access: {error}"}

        if not code:
            return {"success": False, "error": "Missing code parameter"}

        # Validate CSRF state
        if not received_state or not self.state.get(received_state, namespace="twitch_oauth_state"):
            return {"success": False, "error": "Invalid or expired state"}

        self.state.delete(received_state, namespace="twitch_oauth_state")

        try:
            # Exchange code for tokens
            tokens = await self.twitch.exchange_code(code)
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]
            expires_in = tokens.get("expires_in", 14400)
            scopes = tokens.get("scope", [])
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            ).isoformat()

            # Get Twitch user profile
            user_info = await self.twitch.get_user_info(access_token)
            twitch_id = user_info["id"]
            login = user_info["login"]
            display_name = user_info["display_name"]

            # Persist token (upsert by twitch_id)
            existing = await self.db.query_one(
                "SELECT id FROM twitch_tokens WHERE twitch_id = $1", [twitch_id]
            )
            if existing:
                await self.db.execute(
                    """UPDATE twitch_tokens
                       SET login=$1, display_name=$2, access_token=$3,
                           refresh_token=$4, scopes=$5, expires_at=$6,
                           updated_at=datetime('now')
                       WHERE twitch_id=$7""",
                    [login, display_name, access_token, refresh_token,
                     json.dumps(scopes), expires_at, twitch_id],
                )
            else:
                await self.db.execute(
                    """INSERT INTO twitch_tokens
                       (twitch_id, login, display_name, access_token, refresh_token, scopes, expires_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                    [twitch_id, login, display_name, access_token,
                     refresh_token, json.dumps(scopes), expires_at],
                )

            # Connect EventSub + IRC
            await self.twitch.connect(access_token, twitch_id, login)
            self.logger.info(f"[TwitchAuth] Connected as {display_name} ({login})")

            # Redirect browser to frontend instead of returning raw JSON
            frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")
            if context:
                context.redirect(frontend_url)
            return {
                "success": True,
                "data": {"login": login, "display_name": display_name},
            }
        except Exception as e:
            self.logger.error(f"[TwitchOAuthCallback] {e}")
            return {"success": False, "error": str(e)}

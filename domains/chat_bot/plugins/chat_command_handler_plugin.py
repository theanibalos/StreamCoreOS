from datetime import datetime, timezone
from core.base_plugin import BasePlugin


def _format_duration(total_seconds: float) -> str:
    """Convert seconds into a human-readable duration string."""
    total_seconds = int(total_seconds)
    minutes, _ = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    years, days = divmod(days, 365)
    months, days = divmod(days, 30)

    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    if days and not years:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours and not years and not months:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes and not years and not months and not days:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    return ", ".join(parts) if parts else "less than a minute"


class ChatCommandHandlerPlugin(BasePlugin):
    """
    Handles chat commands stored in the DB.

    Subscribes to chat.command.received. For each command:
      1. Looks up the command in DB by name.
      2. Checks per-user cooldown via the state tool.
      3. Resolves dynamic variables in the response template.
      4. Sends the response to chat.
      5. Publishes chat.command.executed.

    Supported variables in command responses:
      {user}      — Display name of the chatter who triggered the command.
      {channel}   — Channel name.
      {followage} — How long the user has been following (e.g. "2 years, 3 months").
      {uptime}    — How long the stream has been live (e.g. "1 hour, 20 minutes").
      {game}      — Current game/category being streamed.
      {viewers}   — Current viewer count.

    Example: "!followage" → response "{user} has been following for {followage}!"
    """

    def __init__(self, twitch, event_bus, db, state, logger):
        self.twitch = twitch
        self.bus = event_bus
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.twitch.require_scopes(["moderator:read:followers"])
        await self.bus.subscribe("chat.command.received", self._handle)

    async def _handle(self, data: dict):
        command_name = data.get("command", "").lower()
        user_id = data.get("user_id", "")
        display_name = data.get("display_name", "")
        channel = data.get("channel", "")

        try:
            cmd = await self.db.query_one(
                "SELECT * FROM chat_commands WHERE name=$1 AND enabled=1",
                [command_name],
            )
            if not cmd:
                return

            # Check cooldown per user
            cooldown_key = f"cmd_cooldown:{command_name}:{user_id}"
            if self.state.get(cooldown_key, namespace="chat_bot"):
                return

            self.state.set(cooldown_key, True, namespace="chat_bot")
            import asyncio
            asyncio.get_event_loop().call_later(
                cmd["cooldown_s"],
                lambda: self.state.delete(cooldown_key, namespace="chat_bot"),
            )

            response = await self._resolve(cmd["response"], data)
            await self.twitch.send_message(channel, response)
            await self.bus.publish("chat.command.executed", {
                "command": command_name,
                "user_id": user_id,
                "display_name": display_name,
                "channel": channel,
            })
        except Exception as e:
            self.logger.error(f"[CommandHandler] Error handling {command_name}: {e}")

    async def _resolve(self, template: str, data: dict) -> str:
        """Replace all {variable} placeholders in the template with live data."""
        result = template

        if "{user}" in result:
            result = result.replace("{user}", data.get("display_name", ""))

        if "{channel}" in result:
            result = result.replace("{channel}", data.get("channel", ""))

        if "{followage}" in result:
            result = result.replace("{followage}", await self._get_followage(data))

        if "{uptime}" in result:
            result = result.replace("{uptime}", self._get_uptime())

        if "{game}" in result or "{viewers}" in result:
            stream_info = await self._get_stream_info()
            result = result.replace("{game}", stream_info.get("game", "Unknown"))
            result = result.replace("{viewers}", str(stream_info.get("viewers", 0)))

        return result

    async def _get_followage(self, data: dict) -> str:
        """Returns how long the chatter has been following, e.g. '2 years, 3 months'."""
        try:
            session = self.twitch.get_session()
            if not session:
                return "unknown"

            broadcaster_id = session["broadcaster_id"]
            user_id = data.get("user_id", "")
            access_token = session["access_token"]

            resp = await self.twitch.get(
                "/channels/followers",
                params={"broadcaster_id": broadcaster_id, "user_id": user_id},
                user_token=access_token,
            )
            followers = resp.get("data", [])
            if not followers:
                return "not following"

            followed_at = datetime.fromisoformat(
                followers[0]["followed_at"].replace("Z", "+00:00")
            )
            delta = datetime.now(timezone.utc) - followed_at
            return _format_duration(delta.total_seconds())
        except Exception as e:
            self.logger.error(f"[CommandHandler] Followage lookup failed: {e}")
            return "unknown"

    def _get_uptime(self) -> str:
        """Returns stream uptime from shared state, e.g. '1 hour, 20 minutes'."""
        try:
            started_at = self.state.get("started_at", namespace="stream_state")
            if not started_at:
                return "offline"
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - started
            return _format_duration(delta.total_seconds())
        except Exception as e:
            self.logger.error(f"[CommandHandler] Uptime calculation failed: {e}")
            return "unknown"

    async def _get_stream_info(self) -> dict:
        """Returns current game name and viewer count from Helix /streams."""
        try:
            session = self.twitch.get_session()
            if not session:
                return {"game": "offline", "viewers": 0}

            broadcaster_id = session["broadcaster_id"]
            access_token = session["access_token"]

            resp = await self.twitch.get(
                "/streams",
                params={"user_id": broadcaster_id},
                user_token=access_token,
            )
            streams = resp.get("data", [])
            if not streams:
                return {"game": "offline", "viewers": 0}

            return {
                "game": streams[0].get("game_name", "Unknown"),
                "viewers": streams[0].get("viewer_count", 0),
            }
        except Exception as e:
            self.logger.error(f"[CommandHandler] Stream info lookup failed: {e}")
            return {"game": "unknown", "viewers": 0}

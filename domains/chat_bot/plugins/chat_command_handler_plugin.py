import re
import random as _random
from datetime import datetime, timezone
from urllib.parse import urlencode
from core.base_plugin import BasePlugin

_VAR_PATTERN = re.compile(r'\{var:([a-z0-9_]+)\}')
_RANDOM_PATTERN = re.compile(r'\{random (\d+)-(\d+)\}')

_LEVELS = ["everyone", "subscriber", "vip", "regular", "moderator", "broadcaster"]


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
      2. Checks userlevel permission.
      3. Checks per-user and global cooldowns via the state tool.
      4. Runs the command's built-in action, if any (see below).
      5. Resolves dynamic variables in the response template.
      6. Sends the response to chat.
      7. Publishes chat.command.executed.

    Built-in actions (chat_commands.action column, chosen at creation time —
    the command's own name/alias, e.g. "!so", is independent of the action):
      shoutout — calls Twitch's Helix POST /chat/shoutouts for the first
                 @mention in the command args (moderator:manage:shoutouts).

    Supported variables in command responses:
      {user}          — Display name of the chatter who triggered the command.
      {touser}        — First @mention after the command, or {user} if none.
      {channel}       — Channel name.
      {count}         — How many times this command has been used (including this one).
      {random X-Y}    — Random integer between X and Y inclusive.
      {followage}     — How long the user has been following.
      {uptime}        — How long the stream has been live.
      {game}          — Current game/category being streamed.
      {viewers}       — Current viewer count.
      {var:name}      — Value of a stream variable from chat_vars table.
    """

    def __init__(self, twitch, event_bus, db, state, logger, youtube=None):
        self.twitch = twitch
        self.youtube = youtube
        self.bus = event_bus
        self.db = db
        self.state = state
        self.logger = logger

    async def on_boot(self):
        self.twitch.require_scopes(["moderator:read:followers", "moderator:manage:shoutouts"])
        if self.youtube:
            try:
                self.youtube.require_scopes(["https://www.googleapis.com/auth/youtube.force-ssl"])
            except Exception:
                pass
        await self.bus.subscribe("chat.command.received", self._handle)

    async def _handle(self, event):
        data = event.payload
        command_name = data.get("command", "").lower()
        user = data.get("user") or {}
        user_id = user.get("id") or data.get("user_id", "")
        display_name = user.get("display_name") or data.get("display_name", "")
        channel = data.get("channel_id") or data.get("channel", "")

        try:
            cmd = await self.db.query_one(
                "SELECT * FROM chat_commands WHERE name=$1 AND enabled=1",
                [command_name],
            )
            if not cmd:
                return

            # Check userlevel
            if not await self._has_permission(data, cmd["userlevel"]):
                return

            # Check per-user cooldown
            user_cooldown_key = f"cmd_cooldown:{command_name}:{user_id}"
            if await self.state.get(user_cooldown_key, namespace="chat_bot"):
                return

            # Check global cooldown
            global_cooldown_key = f"cmd_global:{command_name}"
            if await self.state.get(global_cooldown_key, namespace="chat_bot"):
                return

            # Arm cooldowns via native TTL (state tool now supports it) instead
            # of call_later + lambda — the lambda would return an un-awaited
            # coroutine now that state.set/delete are async.
            if cmd["cooldown_s"] > 0:
                await self.state.set(user_cooldown_key, True, namespace="chat_bot", ttl=cmd["cooldown_s"])
            if cmd["global_cooldown_s"] > 0:
                await self.state.set(global_cooldown_key, True, namespace="chat_bot", ttl=cmd["global_cooldown_s"])

            # Increment use_count and get the new value
            new_count = await self.db.execute(
                "UPDATE chat_commands SET use_count = use_count + 1 WHERE name=$1 RETURNING use_count",
                [command_name],
            )

            if cmd["action"] == "shoutout":
                await self._do_shoutout(data, channel)

            response = await self._resolve(cmd["response"], data, new_count or 1)
            await self._send_reply(data, channel, response)
            await self.bus.publish("chat.command.executed", {
                "command": command_name,
                "user_id": user_id,
                "display_name": display_name,
                "channel": channel,
            })
        except Exception as e:
            self.logger.error(f"[CommandHandler] Error handling {command_name}: {e}")

    async def _send_reply(self, data: dict, channel: str, message: str) -> None:
        if data.get("platform") == "youtube" and self.youtube:
            await self.youtube.send_message(channel, message)
            return
        await self.twitch.send_message(data.get("channel_name") or channel, message)

    async def _do_shoutout(self, data: dict, channel: str) -> None:
        """Resolve the target login from the command args and call Twitch's
        'Send a Shoutout' Helix endpoint (POST /chat/shoutouts). Failures are
        logged but never block the command's chat response."""
        args = data.get("args", "").strip()
        target_login = args.split()[0].lstrip("@").lower() if args else ""
        if not target_login:
            return

        session = self.twitch.get_session()
        if not session:
            return

        try:
            users = await self.twitch.get(
                "/users", params={"login": target_login}, user_token=session["access_token"]
            )
            target = (users.get("data") or [None])[0]
            if not target:
                return

            query = urlencode({
                "from_broadcaster_id": session["broadcaster_id"],
                "to_broadcaster_id": target["id"],
                "moderator_id": session["broadcaster_id"],
            })
            await self.twitch.post(f"/chat/shoutouts?{query}", user_token=session["access_token"])
        except Exception as e:
            body = getattr(getattr(e, "response", None), "text", None)
            detail = f"{e} — {body}" if body else str(e)
            self.logger.error(f"[CommandHandler] Shoutout failed for {target_login}: {detail}")

    async def _resolve(self, template: str, data: dict, count: int) -> str:
        """Replace all {variable} placeholders in the template with live data."""
        result = template

        if "{user}" in result:
            result = result.replace("{user}", (data.get("user") or {}).get("display_name") or data.get("display_name", ""))

        if "{touser}" in result:
            args = data.get("args", "").strip()
            words = args.split()
            touser = words[0].lstrip("@") if words else ((data.get("user") or {}).get("display_name") or data.get("display_name", ""))
            result = result.replace("{touser}", touser)

        if "{channel}" in result:
            result = result.replace("{channel}", data.get("channel_name") or data.get("channel", ""))

        if "{count}" in result:
            result = result.replace("{count}", str(count))

        if _RANDOM_PATTERN.search(result):
            result = _RANDOM_PATTERN.sub(
                lambda m: str(_random.randint(int(m.group(1)), int(m.group(2)))),
                result,
            )

        if "{followage}" in result:
            result = result.replace("{followage}", await self._get_followage(data))

        if "{uptime}" in result:
            result = result.replace("{uptime}", await self._get_uptime())

        if "{game}" in result or "{viewers}" in result:
            stream_info = await self._get_stream_info()
            result = result.replace("{game}", stream_info.get("game", "Unknown"))
            result = result.replace("{viewers}", str(stream_info.get("viewers", 0)))

        if _VAR_PATTERN.search(result):
            result = await self._resolve_vars(result)

        return result

    async def _resolve_vars(self, template: str) -> str:
        """Replace {var:name} placeholders with values from chat_vars table."""
        result = template
        for match in _VAR_PATTERN.finditer(template):
            var_name = match.group(1)
            row = await self.db.query_one(
                "SELECT value FROM chat_vars WHERE name=$1 AND enabled=1", [var_name]
            )
            result = result.replace(match.group(0), row["value"] if row else "?")
        return result

    def _get_user_level(self, data: dict) -> str:
        roles = data.get("roles") or {}
        if roles.get("broadcaster") or data.get("is_broadcaster"):
            return "broadcaster"
        if roles.get("moderator") or data.get("is_mod"):
            return "moderator"
        if roles.get("vip") or data.get("is_vip"):
            return "vip"
        if roles.get("subscriber") or data.get("is_sub"):
            return "subscriber"
        return "everyone"

    async def _has_permission(self, data: dict, required: str) -> bool:
        user_level = self._get_user_level(data)
        try:
            user_idx = _LEVELS.index(user_level)
            required_idx = _LEVELS.index(required)
        except ValueError:
            return False

        if user_idx >= required_idx:
            return True

        # Extra check: is this user in the regulars list?
        if required == "regular":
            row = await self.db.query_one(
                "SELECT id FROM viewers WHERE twitch_id=$1 AND is_regular=1",
                [(data.get("user") or {}).get("id", "")],
            )
            return row is not None

        return False

    async def _get_followage(self, data: dict) -> str:
        try:
            session = self.twitch.get_session()
            if not session:
                return "unknown"

            broadcaster_id = session["broadcaster_id"]
            user_id = (data.get("user") or {}).get("platform_id", "")
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

    async def _get_uptime(self) -> str:
        try:
            started_at = await self.state.get("started_at", namespace="stream_state")
            if not started_at:
                return "offline"
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - started
            return _format_duration(delta.total_seconds())
        except Exception as e:
            self.logger.error(f"[CommandHandler] Uptime calculation failed: {e}")
            return "unknown"

    async def _get_stream_info(self) -> dict:
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

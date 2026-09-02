import re
from datetime import datetime, timedelta
from typing import Optional
from core.base_plugin import BasePlugin


class EchoReminderPlugin(BasePlugin):
    """
    Schedules a delayed message using !echo or !reminder [time] [message].

    Active reminders are tracked in state (namespace "echo", key "active_reminders")
    as a dict keyed by job_id so the list endpoint can expose them.
    """

    def __init__(self, scheduler, twitch, state, event_bus, logger):
        self.scheduler = scheduler
        self.twitch = twitch
        self.state = state
        self.bus = event_bus
        self.logger = logger
        self._duration_regex = re.compile(r"^(\d+)([smh])$")

    async def on_boot(self):
        await self.bus.subscribe("chat.command.received", self._on_command)

    async def _on_command(self, event):
        data = event.payload
        command = data.get("command", "").lower()
        if command not in ["!echo", "!reminder"]:
            return

        roles = data.get("roles") or {}
        is_permitted = (
            roles.get("moderator") or roles.get("broadcaster") or roles.get("vip")
            or data.get("is_mod") or data.get("is_broadcaster") or "vip" in data.get("badges", {})
        )
        if not is_permitted:
            return

        current_count = await self.state.get("echo_count", 0, namespace="echo")
        if current_count >= 3:
            await self._send(data, f"@{(data.get('user') or {}).get('display_name') or data.get('display_name', '')} Falló la programación: Se alcanzó el límite máximo de 3 eco simultáneos. ❌")
            return

        args_str = data.get("args", "")
        if not args_str:
            return

        parts = args_str.split(maxsplit=1)
        time_str = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        if not message:
            return

        seconds = self._parse_duration(time_str)
        if seconds is None:
            self.logger.warning(f"[Echo] Invalid duration: {time_str}")
            return

        display_name = (data.get("user") or {}).get("display_name") or data.get("display_name", "")
        channel = data.get("channel_name") or data.get("channel_id") or data.get("channel", "")
        run_at = datetime.now() + timedelta(seconds=seconds)
        job_id = f"echo_{datetime.now().timestamp()}"

        # Track in state so the list endpoint can expose it
        active = await self.state.get("active_reminders", {}, namespace="echo")
        active[job_id] = {
            "message": message,
            "run_at": run_at.isoformat(),
            "scheduled_by": display_name,
            "channel": channel,
        }
        await self.state.set("active_reminders", active, namespace="echo")
        await self.state.set("echo_count", current_count + 1, namespace="echo")

        route = {
            "platform": data.get("platform", "twitch"),
            "channel_id": data.get("channel_id") or data.get("channel"),
            "channel_name": data.get("channel_name") or data.get("channel"),
        }

        async def _fire():
            await self._send_echo(route, message, job_id)

        self.scheduler.add_one_shot(run_at=run_at, callback=_fire, job_id=job_id)

        await self._send(data, f"@{display_name} Mensaje programado para dentro de {time_str}. 😊")
        self.logger.info(f"[Echo] Scheduled for @{display_name} in {seconds}s")

    async def _send_echo(self, route: dict, message: str, job_id: str):
        try:
            await self.bus.publish("chat.message.send", {**route, "message": message})
        except Exception as e:
            self.logger.error(f"[Echo] Error sending message: {e}")
        finally:
            active = await self.state.get("active_reminders", {}, namespace="echo")
            active.pop(job_id, None)
            await self.state.set("active_reminders", active, namespace="echo")
            count = await self.state.get("echo_count", 0, namespace="echo")
            await self.state.set("echo_count", max(0, count - 1), namespace="echo")

    async def _send(self, data: dict, message: str):
        await self.bus.publish("chat.message.send", {
            "platform": data.get("platform", "twitch"),
            "channel_id": data.get("channel_id") or data.get("channel"),
            "channel_name": data.get("channel_name") or data.get("channel"),
            "message": message,
        })

    def _parse_duration(self, duration_str: str) -> Optional[int]:
        match = self._duration_regex.match(duration_str.lower())
        if not match:
            return None
        value, unit = match.groups()
        value = int(value)
        if unit == "s": return value
        if unit == "m": return value * 60
        if unit == "h": return value * 3600
        return None

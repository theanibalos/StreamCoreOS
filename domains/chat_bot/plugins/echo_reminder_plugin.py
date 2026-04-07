import re
from datetime import datetime, timedelta
from typing import Optional
from core.base_plugin import BasePlugin

class EchoReminderPlugin(BasePlugin):
    """
    Skeleton for the !echo [time] [message] command.
    """

    def __init__(self, scheduler, twitch, event_bus, logger):
        self.scheduler = scheduler
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger
        self._duration_regex = re.compile(r"^(\d+)([smh])$")

    async def on_boot(self):
        # Register for the chat command
        await self.bus.subscribe("chat.command.received", self._on_command)

    async def _on_command(self, data: dict):
        if data.get("command") != "!echo":
            return
        
        # Get the full arguments string and split it
        args_str = data.get("args", "")
        if not args_str:
            return
            
        parts = args_str.split(maxsplit=1)
        time_str = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        
        if not message:
            return

        # Parse duration
        seconds = self._parse_duration(time_str)
        if seconds is None:
            self.logger.warning(f"[Echo] Invalid duration: {time_str}")
            return
            
        # Schedule the echo using the scheduler tool
        run_at = datetime.now() + timedelta(seconds=seconds)
        self.scheduler.add_one_shot(
            run_at=run_at,
            callback=self._send_echo,
            job_id=f"echo_{datetime.now().timestamp()}",
            channel=data["channel"],
            message=message
        )
        
        # Confirmation in chat
        display_name = data.get("display_name")
        await self.twitch.send_message(
            data["channel"],
            f"@{display_name} Mensaje programado para dentro de {time_str}. 😊"
        )
        self.logger.info(f"[Echo] Scheduled for @{display_name} in {seconds}s")

    async def _send_echo(self, channel: str, message: str):
        try:
            await self.twitch.send_message(channel, message)
        except Exception as e:
            self.logger.error(f"[Echo] Error sending message: {e}")

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

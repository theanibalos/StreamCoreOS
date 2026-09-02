from core.base_plugin import BasePlugin


class VarCommandPlugin(BasePlugin):
    """
    Handles stream variable management from chat.

    Commands (mod/broadcaster only):
      !setvar <name> <value>   — Set variable to any value.
      !setvar <name> +<n>      — Increment numeric variable by n (default 1).
      !setvar <name> -<n>      — Decrement numeric variable by n (default 1).
      !setvar <name> reset     — Reset numeric variable to 0.
      !deletevar <name>        — Delete a variable.

    Examples:
      !setvar deaths 0
      !setvar deaths +1
      !setvar deaths reset
      !setvar boss "Margit"
      !deletevar deaths
    """

    def __init__(self, event_bus, db, twitch, logger):
        self.bus = event_bus
        self.db = db
        self.twitch = twitch
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("chat.command.received", self._on_command)

    async def _on_command(self, event):
        data = event.payload
        command = data.get("command", "").lower()
        if command not in ["!setvar", "!deletevar"]:
            return

        if not self._is_permitted(data):
            return

        display_name = (data.get("user") or {}).get("display_name", "")
        args = data.get("args", "").strip()

        if command == "!setvar":
            await self._handle_setvar(data, display_name, args)
        elif command == "!deletevar":
            await self._handle_deletevar(data, display_name, args)

    async def _handle_setvar(self, data: dict, display_name: str, args: str):
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            await self._send(data, f"Uso: !setvar <nombre> <valor>")
            return

        name, raw_value = parts[0].lower(), parts[1].strip()

        existing = await self.db.query_one(
            "SELECT * FROM chat_vars WHERE name=$1", [name]
        )

        new_value = await self._resolve_value(
            raw_value, existing["value"] if existing else "0"
        )

        if existing:
            await self.db.execute(
                "UPDATE chat_vars SET value=$1 WHERE name=$2", [new_value, name]
            )
        else:
            await self.db.execute(
                "INSERT INTO chat_vars (name, value) VALUES ($1, $2)", [name, new_value]
            )

        await self._send(data, f"{name} = {new_value}")
        self.logger.info(f"[VarCommand] @{display_name} set {name} = {new_value}")

    async def _handle_deletevar(self, data: dict, display_name: str, args: str):
        name = args.split()[0].lower() if args.split() else ""
        if not name:
            await self._send(data, "Uso: !deletevar <nombre>")
            return

        affected = await self.db.execute(
            "DELETE FROM chat_vars WHERE name=$1", [name]
        )
        if affected:
            await self._send(data, f"Variable '{name}' eliminada.")
            self.logger.info(f"[VarCommand] @{display_name} deleted {name}")
        else:
            await self._send(data, f"Variable '{name}' no existe.")

    async def _send(self, data: dict, message: str):
        await self.bus.publish("chat.message.send", {
            "platform": data.get("platform", "twitch"),
            "channel_id": data.get("channel_id"),
            "channel_name": data.get("channel_name"),
            "message": message,
        })

    async def _resolve_value(self, raw: str, current: str) -> str:
        """Resolve +n, -n, reset or plain value against the current value."""
        if raw == "reset":
            return "0"

        if raw.startswith(("+", "-")):
            try:
                current_num = float(current)
                delta = float(raw)
                result = current_num + delta
                return str(int(result) if result == int(result) else result)
            except ValueError:
                pass

        return raw

    def _is_permitted(self, data: dict) -> bool:
        roles = data.get("roles") or {}
        return roles.get("moderator") or roles.get("broadcaster") or roles.get("vip")

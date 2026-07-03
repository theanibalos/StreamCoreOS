from core.base_plugin import BasePlugin


class RegularsCommandPlugin(BasePlugin):
    """
    Handles the !regulars chat command (mod/broadcaster only).

    Commands:
      !regulars add @login    — Add a viewer to the regulars list.
                                Looks up by login in viewers table; falls back to Twitch API.
      !regulars remove @login — Remove a viewer from the regulars list.
      !regulars list          — List all current regulars in chat.
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
        if data.get("command", "").lower() != "!regulars":
            return
        if not self._is_permitted(data):
            return

        channel = data.get("channel", "")
        args = data.get("args", "").strip().split(maxsplit=1)
        subcommand = args[0].lower() if args else ""

        if subcommand == "add":
            login = args[1].lstrip("@").lower() if len(args) > 1 else ""
            await self._add(channel, login, data.get("display_name", ""))
        elif subcommand == "remove":
            login = args[1].lstrip("@").lower() if len(args) > 1 else ""
            await self._remove(channel, login)
        elif subcommand == "list":
            await self._list(channel)
        else:
            await self.twitch.send_message(
                channel, "Uso: !regulars add/remove @usuario | !regulars list"
            )

    async def _add(self, channel: str, login: str, added_by: str):
        if not login:
            await self.twitch.send_message(channel, "Uso: !regulars add @usuario")
            return

        try:
            # Try to find by login in viewers table first
            viewer = await self.db.query_one(
                "SELECT twitch_id, login, display_name FROM viewers WHERE login=$1", [login]
            )

            if not viewer:
                # Fall back to Twitch API
                resp = await self.twitch.get("/users", params={"login": login})
                users = resp.get("data", [])
                if not users:
                    await self.twitch.send_message(channel, f"Usuario '{login}' no encontrado.")
                    return
                u = users[0]
                viewer = {"twitch_id": u["id"], "login": u["login"], "display_name": u["display_name"]}

            await self.db.execute(
                """INSERT INTO viewers (twitch_id, login, display_name, is_regular)
                   VALUES ($1, $2, $3, 1)
                   ON CONFLICT(twitch_id) DO UPDATE SET
                       login        = excluded.login,
                       display_name = excluded.display_name,
                       is_regular   = 1""",
                [viewer["twitch_id"], viewer["login"], viewer["display_name"]],
            )
            await self.bus.publish("viewer.regular.added", {
                "twitch_id": viewer["twitch_id"],
                "display_name": viewer["display_name"],
                "added_by": added_by,
            })
            await self.twitch.send_message(
                channel, f"{viewer['display_name']} es ahora un regular."
            )
        except Exception as e:
            self.logger.error(f"[RegularsCommand] add failed: {e}")
            await self.twitch.send_message(channel, "Error al agregar regular.")

    async def _remove(self, channel: str, login: str):
        if not login:
            await self.twitch.send_message(channel, "Uso: !regulars remove @usuario")
            return

        try:
            viewer = await self.db.query_one(
                "SELECT twitch_id, display_name FROM viewers WHERE login=$1 AND is_regular=1",
                [login],
            )
            if not viewer:
                await self.twitch.send_message(channel, f"'{login}' no es un regular.")
                return

            await self.db.execute(
                "UPDATE viewers SET is_regular=0 WHERE twitch_id=$1", [viewer["twitch_id"]]
            )
            await self.bus.publish("viewer.regular.removed", {
                "twitch_id": viewer["twitch_id"],
                "display_name": viewer["display_name"],
            })
            await self.twitch.send_message(
                channel, f"{viewer['display_name']} ya no es un regular."
            )
        except Exception as e:
            self.logger.error(f"[RegularsCommand] remove failed: {e}")
            await self.twitch.send_message(channel, "Error al remover regular.")

    async def _list(self, channel: str):
        try:
            rows = await self.db.query(
                "SELECT display_name FROM viewers WHERE is_regular=1 ORDER BY display_name"
            )
            if not rows:
                await self.twitch.send_message(channel, "No hay regulars.")
                return
            names = ", ".join(r["display_name"] for r in rows)
            await self.twitch.send_message(channel, f"Regulars: {names}")
        except Exception as e:
            self.logger.error(f"[RegularsCommand] list failed: {e}")

    def _is_permitted(self, data: dict) -> bool:
        return data.get("is_mod") or data.get("is_broadcaster")

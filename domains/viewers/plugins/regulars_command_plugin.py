from core.base_plugin import BasePlugin


class RegularsCommandPlugin(BasePlugin):
    """
    Handles !regulars add/remove/list using global_user_id internally.
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

        channel = {
            "platform": data.get("platform", "twitch"),
            "channel_id": data.get("channel_id") or data.get("channel"),
            "channel_name": data.get("channel_name") or data.get("channel"),
        }
        args = data.get("args", "").strip().split(maxsplit=1)
        subcommand = args[0].lower() if args else ""

        if subcommand == "add":
            login = args[1].lstrip("@").lower() if len(args) > 1 else ""
            await self._add(channel, login, (data.get("user") or {}).get("display_name") or data.get("display_name", ""))
        elif subcommand == "remove":
            login = args[1].lstrip("@").lower() if len(args) > 1 else ""
            await self._remove(channel, login)
        elif subcommand == "list":
            await self._list(channel)
        else:
            await self._send(channel, "Uso: !regulars add/remove @usuario | !regulars list")

    async def _add(self, channel: dict, login: str, added_by: str):
        if not login:
            await self._send(channel, "Uso: !regulars add @usuario")
            return

        platform = channel.get("platform") or "twitch"
        try:
            viewer = await self.db.query_one(
                """SELECT global_user_id, platform, platform_user_id, login, display_name
                   FROM viewers WHERE platform=$1 AND lower(login)=lower($2)""",
                [platform, login],
            )

            if not viewer and platform == "twitch":
                resp = await self.twitch.get("/users", params={"login": login})
                users = resp.get("data", [])
                if not users:
                    await self._send(channel, f"Usuario '{login}' no encontrado.")
                    return
                u = users[0]
                viewer = {
                    "global_user_id": f"twitch:{u['id']}",
                    "platform": "twitch",
                    "platform_user_id": u["id"],
                    "login": u["login"],
                    "display_name": u["display_name"],
                }
            elif not viewer:
                await self._send(channel, f"Usuario '{login}' no encontrado en viewers para {platform}.")
                return

            await self.db.execute(
                """INSERT INTO viewers (global_user_id, platform, platform_user_id, login, display_name, is_regular)
                   VALUES ($1, $2, $3, $4, $5, 1)
                   ON CONFLICT(global_user_id) DO UPDATE SET
                       platform         = excluded.platform,
                       platform_user_id = excluded.platform_user_id,
                       login            = excluded.login,
                       display_name     = excluded.display_name,
                       is_regular       = 1""",
                [viewer["global_user_id"], viewer["platform"], viewer["platform_user_id"], viewer["login"], viewer["display_name"]],
            )
            await self.bus.publish("viewer.regular.added", {
                "global_user_id": viewer["global_user_id"],
                "platform": viewer["platform"],
                "platform_user_id": viewer["platform_user_id"],
                "display_name": viewer["display_name"],
                "added_by": added_by,
            })
            await self._send(channel, f"{viewer['display_name']} es ahora un regular.")
        except Exception as e:
            self.logger.error(f"[RegularsCommand] add failed: {e}")
            await self._send(channel, "Error al agregar regular.")

    async def _remove(self, channel: dict, login: str):
        if not login:
            await self._send(channel, "Uso: !regulars remove @usuario")
            return

        platform = channel.get("platform") or "twitch"
        try:
            viewer = await self.db.query_one(
                """SELECT global_user_id, platform, platform_user_id, display_name
                   FROM viewers WHERE platform=$1 AND lower(login)=lower($2) AND is_regular=1""",
                [platform, login],
            )
            if not viewer:
                await self._send(channel, f"'{login}' no es un regular.")
                return

            await self.db.execute(
                "UPDATE viewers SET is_regular=0 WHERE global_user_id=$1", [viewer["global_user_id"]]
            )
            await self.bus.publish("viewer.regular.removed", dict(viewer))
            await self._send(channel, f"{viewer['display_name']} ya no es un regular.")
        except Exception as e:
            self.logger.error(f"[RegularsCommand] remove failed: {e}")
            await self._send(channel, "Error al remover regular.")

    async def _list(self, channel: dict):
        try:
            platform = channel.get("platform") or "twitch"
            rows = await self.db.query(
                "SELECT display_name FROM viewers WHERE platform=$1 AND is_regular=1 ORDER BY display_name",
                [platform],
            )
            if not rows:
                await self._send(channel, "No hay regulars.")
                return
            names = ", ".join(r["display_name"] for r in rows)
            await self._send(channel, f"Regulars: {names}")
        except Exception as e:
            self.logger.error(f"[RegularsCommand] list failed: {e}")

    async def _send(self, channel, message: str):
        await self.bus.publish("chat.message.send", {**channel, "message": message})

    def _is_permitted(self, data: dict) -> bool:
        roles = data.get("roles") or {}
        return roles.get("moderator") or roles.get("broadcaster")

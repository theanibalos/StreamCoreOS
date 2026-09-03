from typing import Optional
from pydantic import BaseModel, Field
from microcoreos.base_plugin import BasePlugin


class AddRegularRequest(BaseModel):
    login: str = Field(min_length=1)
    platform: str = Field(default="twitch", pattern="^(twitch|youtube)$")


class RegularData(BaseModel):
    global_user_id: str
    platform: str
    platform_user_id: str
    login: Optional[str] = None
    display_name: str


class AddRegularResponse(BaseModel):
    success: bool
    data: Optional[RegularData] = None
    error: Optional[str] = None


class AddRegularPlugin(BasePlugin):
    """
    POST /viewers/regulars — Add a viewer to the regulars list.

    Looks up a known viewer by platform/login first. For new manual entries, only
    Twitch can be resolved via API today; YouTube users become regulars after they
    have chatted at least once.
    """

    def __init__(self, http, db, twitch, event_bus, logger):
        self.http = http
        self.db = db
        self.twitch = twitch
        self.bus = event_bus
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/viewers/regulars", "POST", self.execute,
            tags=["Viewers"],
            request_model=AddRegularRequest,
            response_model=AddRegularResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            req = AddRegularRequest(**data)
            login = req.login.lstrip("@").lower()
            platform = req.platform.lower()

            viewer = await self.db.query_one(
                """SELECT global_user_id, platform, platform_user_id, login, display_name
                   FROM viewers WHERE platform=$1 AND lower(login)=lower($2)""",
                [platform, login],
            )
            if not viewer and platform == "twitch":
                resp = await self.twitch.get("/users", params={"login": login})
                users = resp.get("data", [])
                if not users:
                    return {"success": False, "error": f"Usuario '{login}' no encontrado en Twitch."}
                u = users[0]
                viewer = {
                    "global_user_id": f"twitch:{u['id']}",
                    "platform": "twitch",
                    "platform_user_id": u["id"],
                    "login": u["login"],
                    "display_name": u["display_name"],
                }
            elif not viewer:
                return {"success": False, "error": f"Usuario '{login}' no encontrado en viewers para {platform}."}

            await self.db.execute(
                """INSERT INTO viewers (global_user_id, platform, platform_user_id, login, display_name, is_regular)
                   VALUES ($1, $2, $3, $4, $5, 1)
                   ON CONFLICT(global_user_id) DO UPDATE SET
                       platform         = excluded.platform,
                       platform_user_id = excluded.platform_user_id,
                       login            = excluded.login,
                       display_name     = excluded.display_name,
                       is_regular       = 1""",
                [
                    viewer["global_user_id"],
                    viewer["platform"],
                    viewer["platform_user_id"],
                    viewer["login"],
                    viewer["display_name"],
                ],
            )
            await self.bus.publish("viewer.regular.added", {
                "global_user_id": viewer["global_user_id"],
                "platform": viewer["platform"],
                "platform_user_id": viewer["platform_user_id"],
                "display_name": viewer["display_name"],
            })
            return {"success": True, "data": dict(viewer)}
        except Exception as e:
            self.logger.error(f"[AddRegular] {e}")
            return {"success": False, "error": str(e)}

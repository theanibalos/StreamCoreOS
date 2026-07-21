from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class AddRegularRequest(BaseModel):
    login: str = Field(min_length=1)


class RegularData(BaseModel):
    twitch_id: str
    login: str
    display_name: str


class AddRegularResponse(BaseModel):
    success: bool
    data: Optional[RegularData] = None
    error: Optional[str] = None


class AddRegularPlugin(BasePlugin):
    """
    POST /viewers/regulars — Add a viewer to the regulars list by username.

    Looks up the login in the viewers table first (already chatted before);
    falls back to the Twitch API to resolve twitch_id/display_name otherwise.
    Upserts the viewer record, then sets is_regular=1.
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

            viewer = await self.db.query_one(
                "SELECT twitch_id, login, display_name FROM viewers WHERE login=$1", [login]
            )
            if not viewer:
                resp = await self.twitch.get("/users", params={"login": login})
                users = resp.get("data", [])
                if not users:
                    return {"success": False, "error": f"Usuario '{login}' no encontrado en Twitch."}
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
            })
            return {"success": True, "data": {
                "twitch_id": viewer["twitch_id"],
                "login": viewer["login"],
                "display_name": viewer["display_name"],
            }}
        except Exception as e:
            self.logger.error(f"[AddRegular] {e}")
            return {"success": False, "error": str(e)}

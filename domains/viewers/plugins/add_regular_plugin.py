from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class AddRegularRequest(BaseModel):
    twitch_id: str = Field(min_length=1)
    login: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


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
    POST /viewers/regulars — Add a viewer to the regulars list.

    Upserts the viewer record if they haven't chatted yet,
    then sets is_regular=1.
    """

    def __init__(self, http, db, event_bus, logger):
        self.http = http
        self.db = db
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
            await self.db.execute(
                """INSERT INTO viewers (twitch_id, login, display_name, is_regular)
                   VALUES ($1, $2, $3, 1)
                   ON CONFLICT(twitch_id) DO UPDATE SET
                       login        = excluded.login,
                       display_name = excluded.display_name,
                       is_regular   = 1""",
                [req.twitch_id, req.login, req.display_name],
            )
            await self.bus.publish("viewer.regular.added", {
                "twitch_id": req.twitch_id,
                "display_name": req.display_name,
            })
            return {"success": True, "data": {
                "twitch_id": req.twitch_id,
                "login": req.login,
                "display_name": req.display_name,
            }}
        except Exception as e:
            self.logger.error(f"[AddRegular] {e}")
            return {"success": False, "error": str(e)}

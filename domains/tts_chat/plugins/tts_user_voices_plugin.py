from typing import Optional
from pydantic import BaseModel, Field
from core.base_plugin import BasePlugin


class UserVoiceItem(BaseModel):
    id:           int
    twitch_id:    str
    twitch_login: str
    voice_id:     str
    voice_name:   str
    updated_at:   str


class UserVoiceListResponse(BaseModel):
    success: bool
    data:    Optional[list[UserVoiceItem]] = None
    error:   Optional[str] = None


class UserVoiceResponse(BaseModel):
    success: bool
    data:    Optional[UserVoiceItem] = None
    error:   Optional[str] = None


class UpsertUserVoiceRequest(BaseModel):
    twitch_id:    str = Field(min_length=1, max_length=50)
    twitch_login: str = Field(min_length=1, max_length=50)
    voice_id:     str = Field(min_length=1, max_length=200)
    voice_name:   str = Field(min_length=1, max_length=200)


class SimpleResponse(BaseModel):
    success: bool
    error:   Optional[str] = None


class TtsUserVoicesPlugin(BasePlugin):
    """
    GET    /tts/user-voices                     — list all user→voice assignments
    GET    /tts/user-voices/{twitch_login}       — get a single assignment
    PUT    /tts/user-voices                      — upsert an assignment
    DELETE /tts/user-voices/{twitch_login}       — remove assignment
    """

    def __init__(self, db, http, logger):
        self.db     = db
        self.http   = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/tts/user-voices", "GET", self.list_assignments,
            tags=["TTS"],
            response_model=UserVoiceListResponse,
        )
        self.http.add_endpoint(
            "/tts/user-voices/{twitch_login}", "GET", self.get_assignment,
            tags=["TTS"],
            response_model=UserVoiceResponse,
        )
        self.http.add_endpoint(
            "/tts/user-voices", "PUT", self.upsert_assignment,
            tags=["TTS"],
            request_model=UpsertUserVoiceRequest,
            response_model=UserVoiceResponse,
        )
        self.http.add_endpoint(
            "/tts/user-voices/{twitch_login}", "DELETE", self.delete_assignment,
            tags=["TTS"],
            response_model=SimpleResponse,
        )

    async def list_assignments(self, data: dict, context=None):
        rows = await self.db.query(
            "SELECT * FROM tts_user_voice ORDER BY twitch_login ASC"
        )
        return {"success": True, "data": rows}

    async def get_assignment(self, data: dict, context=None):
        login = data.get("twitch_login", "")
        row   = await self.db.query_one(
            "SELECT * FROM tts_user_voice WHERE twitch_login = $1", [login]
        )
        if not row:
            return {"success": False, "error": f"No voice assignment for '{login}'"}
        return {"success": True, "data": row}

    async def upsert_assignment(self, data: dict, context=None):
        try:
            req = UpsertUserVoiceRequest(**data)
            await self.db.execute(
                """
                INSERT INTO tts_user_voice (twitch_id, twitch_login, voice_id, voice_name, updated_at)
                VALUES ($1, $2, $3, $4, datetime('now'))
                ON CONFLICT (twitch_id) DO UPDATE SET
                    twitch_login = excluded.twitch_login,
                    voice_id     = excluded.voice_id,
                    voice_name   = excluded.voice_name,
                    updated_at   = datetime('now')
                """,
                [req.twitch_id, req.twitch_login, req.voice_id, req.voice_name],
            )
            row = await self.db.query_one(
                "SELECT * FROM tts_user_voice WHERE twitch_id = $1", [req.twitch_id]
            )
            self.logger.info(f"[TTS] Voice assigned: {req.twitch_login} → {req.voice_id}")
            return {"success": True, "data": row}
        except Exception as e:
            self.logger.error(f"[TTS] upsert_assignment error: {e}")
            return {"success": False, "error": str(e)}

    async def delete_assignment(self, data: dict, context=None):
        login = data.get("twitch_login", "")
        count = await self.db.execute(
            "DELETE FROM tts_user_voice WHERE twitch_login = $1", [login]
        )
        if not count:
            return {"success": False, "error": f"No assignment found for '{login}'"}
        self.logger.info(f"[TTS] Voice assignment removed for {login}")
        return {"success": True}

import os
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

UPLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../uploads/backgrounds"))

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".webm"}


class BackgroundFileInfo(BaseModel):
    filename: str
    url: str
    type: str
    size: int


class ListBackgroundsResponse(BaseModel):
    success: bool
    data: Optional[list[BackgroundFileInfo]] = None
    error: Optional[str] = None


class ListBackgroundsPlugin(BasePlugin):
    def __init__(self, http, logger):
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/backgrounds", "GET", self.execute,
            tags=["Overlays"],
            response_model=ListBackgroundsResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            os.makedirs(UPLOADS_DIR, exist_ok=True)
            files = []
            for name in sorted(os.listdir(UPLOADS_DIR)):
                ext = os.path.splitext(name)[1].lower()
                if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
                    continue
                kind = "video" if ext in VIDEO_EXTS else "image"
                size = os.path.getsize(os.path.join(UPLOADS_DIR, name))
                files.append({
                    "filename": name,
                    "url": f"/api/uploads/backgrounds/{name}",
                    "type": kind,
                    "size": size,
                })
            return {"success": True, "data": files}
        except Exception as e:
            self.logger.error(f"[ListBackgrounds] {e}")
            return {"success": False, "error": str(e)}

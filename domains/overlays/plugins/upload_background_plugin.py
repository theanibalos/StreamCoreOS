import os
import uuid
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "../../../uploads/backgrounds")
UPLOADS_DIR = os.path.abspath(UPLOADS_DIR)

ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


class UploadBackgroundData(BaseModel):
    url: str
    type: str


class UploadBackgroundResponse(BaseModel):
    success: bool
    data: Optional[UploadBackgroundData] = None
    error: Optional[str] = None


class UploadBackgroundPlugin(BasePlugin):
    """Handles background media uploads for the overlay builder."""

    def __init__(self, http, logger):
        self.http = http
        self.logger = logger

    async def on_boot(self):
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        self.http.mount_static("/api/uploads", os.path.join(UPLOADS_DIR, ".."))
        self.http.add_endpoint(
            "/api/overlays/upload-background", "POST", self.execute,
            tags=["Overlays"],
            response_model=UploadBackgroundResponse,
            has_files=True,
        )

    async def execute(self, data: dict, context=None):
        try:
            files = data.get("_files") or []
            file = next((f for f in files if f is not None), None)
            if not file:
                context.set_status(400)
                return {"success": False, "error": "No file provided"}

            content_type = file.content_type or ""
            ext = ALLOWED_TYPES.get(content_type)
            if not ext:
                context.set_status(415)
                return {"success": False, "error": f"Tipo no permitido: {content_type}"}

            filename = f"{uuid.uuid4().hex}{ext}"
            dest = os.path.join(UPLOADS_DIR, filename)

            contents = await file.read()
            with open(dest, "wb") as f:
                f.write(contents)

            media_kind = "video" if content_type.startswith("video/") else "image"
            url = f"/api/uploads/backgrounds/{filename}"
            return {"success": True, "data": {"url": url, "type": media_kind}}

        except Exception as e:
            self.logger.error(f"[UploadBackground] {e}")
            return {"success": False, "error": str(e)}

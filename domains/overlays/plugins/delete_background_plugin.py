import os
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

UPLOADS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../uploads/backgrounds"))


class DeleteBackgroundResponse(BaseModel):
    success: bool
    error: Optional[str] = None


class DeleteBackgroundPlugin(BasePlugin):
    def __init__(self, http, logger):
        self.http = http
        self.logger = logger

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/overlays/backgrounds/{filename}", "DELETE", self.execute,
            tags=["Overlays"],
            response_model=DeleteBackgroundResponse,
        )

    async def execute(self, data: dict, context=None):
        try:
            filename = data.get("filename", "")
            # Prevent path traversal
            if "/" in filename or "\\" in filename or ".." in filename:
                context.set_status(400)
                return {"success": False, "error": "Nombre de archivo inválido"}

            path = os.path.join(UPLOADS_DIR, filename)
            if not os.path.isfile(path):
                context.set_status(404)
                return {"success": False, "error": "Archivo no encontrado"}

            os.remove(path)
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[DeleteBackground] {e}")
            return {"success": False, "error": str(e)}

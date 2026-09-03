import os
import shutil
import subprocess
from typing import Optional
from pydantic import BaseModel
from microcoreos.base_plugin import BasePlugin

MEDIA_DIR = os.path.abspath("media")
FALLBACK_VIDEO_PATH = os.path.join(MEDIA_DIR, "fallback.mp4")


class UploadFallbackVideoData(BaseModel):
    mode: str
    video_path: str
    configured: bool


class UploadFallbackVideoResponse(BaseModel):
    success: bool
    data: Optional[UploadFallbackVideoData] = None
    error: Optional[str] = None


class UploadFallbackVideoPlugin(BasePlugin):
    """POST /stream-outputs/fallback/video — Upload MP4 standby/fallback video."""

    def __init__(self, http, stream_tool, logger):
        self.http = http
        self.stream_tool = stream_tool
        self.logger = logger

    async def on_boot(self):
        os.makedirs(MEDIA_DIR, exist_ok=True)
        self.http.mount_static("/api/stream-media", MEDIA_DIR)
        self.http.add_endpoint(
            "/api/stream-outputs/fallback/video", "POST", self.execute,
            tags=["Stream Outputs"], response_model=UploadFallbackVideoResponse,
            has_files=True,
        )

    async def execute(self, data: dict, context=None):
        try:
            files = data.get("_files") or []
            file = next((f for f in files if f is not None), None)
            if not file:
                if context:
                    context.set_status(400)
                return {"success": False, "error": "No file provided"}

            content_type = file.content_type or ""
            filename = (file.filename or "").lower()
            if content_type != "video/mp4" and not filename.endswith(".mp4"):
                if context:
                    context.set_status(415)
                return {"success": False, "error": "Solo se permite video MP4 para fallback."}

            contents = await file.read()
            if not contents:
                if context:
                    context.set_status(400)
                return {"success": False, "error": "El archivo está vacío."}

            tmp_path = FALLBACK_VIDEO_PATH + ".tmp"
            with open(tmp_path, "wb") as f:
                f.write(contents)

            # Auto-optimize to standard 1080p H.264 + AAC stereo with faststart
            optimized_path = FALLBACK_VIDEO_PATH + ".opt.mp4"
            ffmpeg_path = shutil.which("ffmpeg")
            if ffmpeg_path:
                try:
                    subprocess.run(
                        [
                            ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                            "-i", tmp_path,
                            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                            "-map", "0:v:0", "-map", "1:a:0",
                            "-c:v", "libopenh264",
                            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black",
                            "-r", "30", "-g", "60", "-keyint_min", "60",
                            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                            "-shortest", "-movflags", "+faststart",
                            optimized_path
                        ],
                        timeout=30
                    )
                    if os.path.exists(optimized_path) and os.path.getsize(optimized_path) > 0:
                        os.replace(optimized_path, tmp_path)
                except Exception as opt_err:
                    self.logger.warning(f"[UploadFallbackVideo] Optimization notice: {opt_err}")

            os.replace(tmp_path, FALLBACK_VIDEO_PATH)

            relative_path = "media/fallback.mp4"
            await self.stream_tool.set_fallback_video(relative_path)
            return {
                "success": True,
                "data": {"mode": "video", "video_path": relative_path, "configured": True},
            }
        except Exception as e:
            self.logger.error(f"[UploadFallbackVideo] {e}")
            return {"success": False, "error": str(e)}

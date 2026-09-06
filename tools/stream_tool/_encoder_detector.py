import shutil
import subprocess
from typing import Optional


class EncoderDetector:
    _cached_encoders: Optional[list] = None
    _recommended: Optional[str] = None

    @classmethod
    def detect(cls, ffmpeg_path: Optional[str] = None) -> tuple[list, str]:
        if cls._cached_encoders is not None and cls._recommended is not None:
            return cls._cached_encoders, cls._recommended

        if not ffmpeg_path:
            ffmpeg_path = shutil.which("ffmpeg")

        if not ffmpeg_path:
            return [], "copy"

        try:
            res = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = res.stdout
        except Exception:
            output = ""

        available_map = {
            "h264_nvenc": {"id": "h264_nvenc", "label": "NVIDIA NVENC (GPU Hardware)", "type": "hardware", "vendor": "nvidia"},
            "h264_vaapi": {"id": "h264_vaapi", "label": "VAAPI (Linux Intel/AMD GPU)", "type": "hardware", "vendor": "vaapi"},
            "h264_qsv": {"id": "h264_qsv", "label": "Intel QuickSync (GPU Hardware)", "type": "hardware", "vendor": "intel"},
            "h264_amf": {"id": "h264_amf", "label": "AMD AMF (GPU Hardware)", "type": "hardware", "vendor": "amd"},
            "libx264": {"id": "libx264", "label": "CPU (libx264 Software)", "type": "software", "vendor": "cpu"},
        }

        found = []
        for enc_id, meta in available_map.items():
            if enc_id in output:
                found.append(meta)

        recommended = "libx264"
        priority = ["h264_nvenc", "h264_vaapi", "h264_qsv", "h264_amf", "libx264"]
        for p in priority:
            if any(e["id"] == p for e in found):
                recommended = p
                break

        cls._cached_encoders = found
        cls._recommended = recommended
        return found, recommended

    @classmethod
    def get_encoder_args(cls, encoder: str, bitrate_kbps: int = 6000) -> list[str]:
        b = max(1000, min(12000, bitrate_kbps))
        maxrate = int(b * 1.1)
        bufsize = int(b * 2)

        if encoder == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-tune", "ll",
                "-rc", "cbr",
                "-b:v", f"{b}k",
                "-maxrate", f"{maxrate}k",
                "-bufsize", f"{bufsize}k",
                "-pix_fmt", "yuv420p",
                "-g", "120",
            ]
        elif encoder == "h264_vaapi":
            return [
                "-vaapi_device", "/dev/dri/renderD128",
                "-vf", "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-b:v", f"{b}k",
                "-maxrate", f"{maxrate}k",
                "-g", "120",
            ]
        elif encoder == "h264_qsv":
            return [
                "-c:v", "h264_qsv",
                "-preset", "veryfast",
                "-b:v", f"{b}k",
                "-maxrate", f"{maxrate}k",
                "-bufsize", f"{bufsize}k",
                "-pix_fmt", "nv12",
                "-g", "120",
            ]
        elif encoder == "h264_amf":
            return [
                "-c:v", "h264_amf",
                "-quality", "speed",
                "-b:v", f"{b}k",
                "-g", "120",
            ]
        else:
            return [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-b:v", f"{b}k",
                "-maxrate", f"{maxrate}k",
                "-bufsize", f"{bufsize}k",
                "-pix_fmt", "yuv420p",
                "-g", "120",
            ]

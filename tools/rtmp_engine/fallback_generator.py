import os
import shutil
import subprocess
import threading
import logging
from typing import Optional, Callable

logger = logging.getLogger("StreamOS.FallbackGenerator")


class FallbackGenerator:
    """
    Fallback Generator:
    Runs a lightweight background FFmpeg process that generates a continuous FLV stream
    (Image + MP3 loop or Video MP4 loop) to feed active relays when OBS is disconnected.
    """

    def __init__(self, broadcast_callback: Callable[[bytes], None]):
        self.broadcast_callback = broadcast_callback
        self.ffmpeg_path = shutil.which("ffmpeg")
        self.process: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False
        self._encoder = self._detect_best_encoder()

    def _detect_best_encoder(self) -> str:
        if not self.ffmpeg_path:
            return "libx264"
        try:
            res = subprocess.run([self.ffmpeg_path, "-encoders"], capture_output=True, text=True, timeout=5)
            output = res.stdout or ""
            # Prioritize NVIDIA NVENC for zero-CPU, high-bitrate CBR encoding
            if "h264_nvenc" in output:
                test = subprocess.run(
                    [self.ffmpeg_path, "-hide_banner", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=1", "-t", "0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                    capture_output=True, timeout=3
                )
                if test.returncode == 0:
                    logger.info("[FallbackGenerator] Using hardware NVIDIA NVENC encoder (h264_nvenc).")
                    return "h264_nvenc"
            if "libx264" in output:
                return "libx264"
            if "libopenh264" in output:
                return "libopenh264"
        except Exception:
            pass
        return "libopenh264"

    def _video_has_audio(self, video_path: str) -> bool:
        if not self.ffmpeg_path or not os.path.exists(video_path):
            return False
        ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path:
            return False
        try:
            res = subprocess.run(
                [ffprobe_path, "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=3
            )
            return "audio" in (res.stdout or "")
        except Exception:
            return False

    def _video_is_h264(self, video_path: str) -> bool:
        if not self.ffmpeg_path or not os.path.exists(video_path):
            return False
        ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path:
            return False
        try:
            res = subprocess.run(
                [ffprobe_path, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", video_path],
                capture_output=True, text=True, timeout=3
            )
            codec = (res.stdout or "").strip().lower()
            return codec in ("h264", "avc1")
        except Exception:
            return False

    def _ensure_default_image(self, target_path: str):
        if not os.path.exists(target_path):
            dir_name = os.path.dirname(target_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            # Create a clean fallback image with ffmpeg
            try:
                subprocess.run(
                    [
                        self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=0x0f172a:s=1920x1080:d=1",
                        "-frames:v", "1", target_path
                    ],
                    timeout=5
                )
            except Exception:
                pass

    def _resolve_media_paths(self, config: dict) -> tuple[Optional[str], Optional[str]]:
        image_path = config.get("image_path", "media/standby.jpg")
        audio_path = config.get("audio_path", "media/music.mp3")

        # If specified image doesn't exist, search media/ for any valid image
        if not image_path or not os.path.exists(image_path):
            candidates = ["media/standby.jpg", "media/standby.png", "media/fallback.jpg"]
            if os.path.isdir("media"):
                candidates += [
                    os.path.join("media", f)
                    for f in sorted(os.listdir("media"))
                    if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                ]
            for c in candidates:
                if os.path.exists(c):
                    image_path = c
                    break

        # If specified audio doesn't exist, search media/ for any valid audio
        if not audio_path or not os.path.exists(audio_path):
            candidates = ["media/music.mp3", "media/music.wav", "media/music.aac"]
            if os.path.isdir("media"):
                candidates += [
                    os.path.join("media", f)
                    for f in sorted(os.listdir("media"))
                    if f.lower().endswith((".mp3", ".wav", ".aac", ".m4a", ".ogg"))
                ]
            for c in candidates:
                if os.path.exists(c):
                    audio_path = c
                    break

        return image_path, audio_path

    def start(self, config: dict):
        if not self.ffmpeg_path:
            logger.error("FFmpeg not installed, cannot start fallback generator.")
            return

        if self.is_running:
            self.stop()

        mode = config.get("mode", "video" if os.path.exists("media/fallback.mp4") else "image_audio")
        if mode == "disabled":
            return

        encoder = self._encoder
        encoder_args = ["-c:v", encoder, "-pix_fmt", "yuv420p"]
        if encoder == "h264_nvenc":
            encoder_args.extend(["-preset", "p4", "-tune", "ll", "-rc", "cbr"])
        elif encoder == "libx264":
            encoder_args.extend(["-preset", "ultrafast", "-tune", "zerolatency"])
        elif encoder == "libopenh264":
            encoder_args.extend(["-rc_mode", "bitrate", "-allow_skip_frames", "1"])

        cmd = []
        if mode == "image_audio" or mode != "video":
            image_path, audio_path = self._resolve_media_paths(config)

            if image_path:
                self._ensure_default_image(image_path)
            else:
                image_path = "media/standby.jpg"
                self._ensure_default_image(image_path)

            has_image = os.path.exists(image_path) if image_path else False
            has_audio = os.path.exists(audio_path) if audio_path else False

            logger.info(f"[FallbackGenerator] Using image: '{image_path}' (exists={has_image}), audio: '{audio_path}' (exists={has_audio})")

            youtube_video_args = [
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                "-r", "30", "-g", "30", "-keyint_min", "30",
                "-b:v", "6000k", "-minrate", "4500k", "-maxrate", "6800k", "-bufsize", "12000k",
            ]
            youtube_audio_args = ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]

            if has_image and has_audio:
                cmd = [
                    self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-re", "-loop", "1", "-i", image_path,
                    "-stream_loop", "-1", "-re", "-i", audio_path,
                    "-map", "0:v:0", "-map", "1:a:0",
                    *encoder_args, *youtube_video_args, *youtube_audio_args,
                    "-flvflags", "no_sequence_end",
                    "-f", "flv", "pipe:1"
                ]
            elif has_image:
                cmd = [
                    self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-re", "-loop", "1", "-i", image_path,
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-map", "0:v:0", "-map", "1:a:0",
                    *encoder_args, *youtube_video_args, *youtube_audio_args,
                    "-flvflags", "no_sequence_end",
                    "-f", "flv", "pipe:1"
                ]
            else:
                cmd = [
                    self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                    "-re", "-f", "lavfi", "-i", "color=c=0x0f172a:s=1920x1080:r=30",
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-map", "0:v:0", "-map", "1:a:0",
                    *encoder_args, "-g", "60", "-b:v", "6800k", "-maxrate", "6800k", "-bufsize", "13600k", *youtube_audio_args,
                    "-flvflags", "no_sequence_end",
                    "-f", "flv", "pipe:1"
                ]

        elif mode == "video":
            video_path = config.get("video_path", "media/fallback.mp4")
            if os.path.exists(video_path):
                has_audio = self._video_has_audio(video_path)
                # If already standard H.264 + AAC, use ultra-fast passthrough (-c copy)
                is_h264 = self._video_is_h264(video_path)
                if has_audio and is_h264:
                    cmd = [
                        self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                        "-re", "-stream_loop", "-1", "-i", video_path,
                        "-c", "copy",
                        "-flvflags", "no_sequence_end",
                        "-f", "flv", "pipe:1"
                    ]
                elif has_audio:
                    cmd = [
                        self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                        "-re", "-stream_loop", "-1", "-i", video_path,
                        *encoder_args,
                        "-r", "30", "-g", "60",
                        "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
                        "-flvflags", "no_sequence_end",
                        "-f", "flv", "pipe:1"
                    ]
                else:
                    cmd = [
                        self.ffmpeg_path, "-hide_banner", "-loglevel", "error",
                        "-re", "-stream_loop", "-1", "-i", video_path,
                        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-map", "0:v:0", "-map", "1:a:0",
                        *encoder_args,
                        "-r", "30", "-g", "60",
                        "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000",
                        "-flvflags", "no_sequence_end",
                        "-f", "flv", "pipe:1"
                    ]
            else:
                logger.warning(f"Fallback video path '{video_path}' does not exist. Falling back to image_audio.")
                config["mode"] = "image_audio"
                self.start(config)
                return

        if not cmd:
            return

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0
            )
            self.is_running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            logger.info(f"FallbackGenerator started successfully in '{mode}' mode using encoder '{encoder}'.")
        except Exception as e:
            logger.error(f"Error starting FallbackGenerator: {e}")
            self.is_running = False

    def _read_loop(self):
        bufsize = 4096
        while self.is_running and self.process and self.process.poll() is None:
            try:
                chunk = self.process.stdout.read(bufsize)
                if not chunk:
                    break
                self.broadcast_callback(chunk)
            except Exception as e:
                logger.error(f"Error reading fallback stream: {e}")
                break
        self.is_running = False

    def stop(self):
        self.is_running = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None
        self.thread = None
        logger.info("FallbackGenerator stopped.")

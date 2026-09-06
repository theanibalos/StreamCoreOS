"""
RTMP Engine Tool — StreamOS RTMP Server & Relay Manager
=========================================================

Embedded RTMP Ingress Server (Port 1935) + High-efficiency FFmpeg
passthrough relays with sequence header caching and standby fallback support.
"""

import os
import shutil
import time
import subprocess
import threading
import logging
from typing import Dict, Any, Optional
from microcoreos.base_tool import BaseTool
from tools.rtmp_engine.rtmp_server import RtmpIngressServer
from tools.rtmp_engine.fallback_generator import FallbackGenerator

logger = logging.getLogger("StreamOS.RtmpEngine")


class FLVTagStreamProcessor:
    """
    Processes incoming raw FLV stream chunks to ensure smooth timestamp continuity
    and strip mid-stream FLV file headers when transitioning between OBS and Fallback.
    Tracks video and audio timestamps independently to prevent drift.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.last_video_ts = -1
        self.last_audio_ts = -1
        self.base_offset = 0
        self.source_video_start_ts = None
        self.source_audio_start_ts = None
        self.header_stripped = False
        self.cached_metadata: Optional[bytes] = None
        self.cached_avc_config: Optional[bytes] = None
        self.cached_aac_config: Optional[bytes] = None

    def reset_source(self):
        """Reset source timestamp tracking and header stripping for stream switch."""
        self.source_video_start_ts = None
        self.source_audio_start_ts = None
        highest_ts = max(self.last_video_ts, self.last_audio_ts)
        self.base_offset = highest_ts + 33 if highest_ts > 0 else 0
        self.header_stripped = False
        self.buffer.clear()

    def process_bytes(self, chunk: bytes) -> bytes:
        self.buffer.extend(chunk)

        # Strip 13-byte FLV file header whenever an FLV header is encountered in the stream buffer
        while len(self.buffer) >= 13 and self.buffer[:3] == b'FLV':
            del self.buffer[:13]

        out = bytearray()

        while True:
            if len(self.buffer) < 11:
                break

            tag_type = self.buffer[0]
            data_size = (self.buffer[1] << 16) | (self.buffer[2] << 8) | self.buffer[3]
            total_tag_size = 11 + data_size + 4

            if len(self.buffer) < total_tag_size:
                break

            tag_data = self.buffer[:total_tag_size]
            del self.buffer[:total_tag_size]

            # Read 32-bit FLV timestamp (tag_data[4..7]: 24-bit lower + 8-bit upper in byte 7)
            raw_ts = (tag_data[7] << 24) | (tag_data[4] << 16) | (tag_data[5] << 8) | tag_data[6]

            if tag_type == 9:  # Video
                if self.source_video_start_ts is None:
                    self.source_video_start_ts = raw_ts
                relative_ts = max(0, raw_ts - self.source_video_start_ts)
                new_ts = self.base_offset + relative_ts
                if self.last_video_ts >= 0 and new_ts <= self.last_video_ts:
                    new_ts = self.last_video_ts + 1
                self.last_video_ts = new_ts
                # Detect and cache AVC Sequence Header (SPS/PPS)
                if len(tag_data) >= 13 and tag_data[11] == 0x17 and tag_data[12] == 0x00:
                    self.cached_avc_config = bytes(tag_data)

            elif tag_type == 8:  # Audio
                if self.source_audio_start_ts is None:
                    self.source_audio_start_ts = raw_ts
                relative_ts = max(0, raw_ts - self.source_audio_start_ts)
                new_ts = self.base_offset + relative_ts
                if self.last_audio_ts >= 0 and new_ts <= self.last_audio_ts:
                    new_ts = self.last_audio_ts + 1
                self.last_audio_ts = new_ts
                # Detect and cache AAC Sequence Header (AudioSpecificConfig)
                if len(tag_data) >= 13 and (tag_data[11] & 0xF0) == 0xA0 and tag_data[12] == 0x00:
                    self.cached_aac_config = bytes(tag_data)

            else:  # Script / Metadata / Other
                new_ts = max(self.last_video_ts, self.last_audio_ts, 0)
                if tag_type == 18:
                    self.cached_metadata = bytes(tag_data)

            # Re-pack 32-bit timestamp
            tag_data[4] = (new_ts >> 16) & 0xFF
            tag_data[5] = (new_ts >> 8) & 0xFF
            tag_data[6] = new_ts & 0xFF
            tag_data[7] = (new_ts >> 24) & 0xFF

            out.extend(tag_data)

        return bytes(out)


class RtmpEngineTool(BaseTool):
    """
    RTMP Engine Tool (rtmp_engine):
    Embedded RTMP Ingress server + FFmpeg relay manager with fallback support.
    """

    def __init__(self):
        super().__init__()
        self._relays: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()  # Reentrant Lock (RLock) to prevent reentrant deadlocks
        self._ffmpeg_path = shutil.which("ffmpeg")
        self._server: Optional[RtmpIngressServer] = None
        self._obs_connected = False
        self._obs_stream_key = ""
        self._last_obs_packet_at = 0.0
        self._watchdog_active = True
        
        # Header cache for mid-stream relay initialization
        self._flv_header: Optional[bytes] = None
        self._metadata_bytes: Optional[bytes] = None
        self._avc_config_bytes: Optional[bytes] = None
        self._aac_config_bytes: Optional[bytes] = None

        # FLV Tag Stream Processor for smooth transitions
        self._flv_processor = FLVTagStreamProcessor()

        # Standby Fallback configuration & generator
        default_mode = "video" if os.path.exists("media/fallback.mp4") else "image_audio"
        self._fallback_config = {
            "mode": default_mode,
            "image_path": "media/standby.jpg",
            "audio_path": "media/music.mp3",
            "video_path": "media/fallback.mp4"
        }
        self._fallback_generator = FallbackGenerator(self.broadcast_flv_bytes)

        # Start watchdog background thread
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()

    @property
    def name(self) -> str:
        return "rtmp_engine"

    async def setup(self) -> None:
        """Start the embedded RTMP Ingress Server on port 1935."""
        self._server = RtmpIngressServer(self, host="0.0.0.0", port=1935)
        await self._server.start()

    async def close(self) -> None:
        """Gracefully shut down RTMP server, active relays, and standby loops on Ctrl+C."""
        self._watchdog_active = False
        try:
            self.stop_all_relays()
        except Exception:
            pass
        if self._server:
            try:
                await self._server.stop()
            except Exception:
                pass

    async def shutdown(self) -> None:
        await self.close()

    def _watchdog_loop(self):
        """Monitors OBS stream packets. Triggers fallback if OBS stops sending data for >3.5s."""
        while self._watchdog_active:
            time.sleep(1.0)
            with self._lock:
                if self._obs_connected and self._last_obs_packet_at > 0:
                    silence_duration = time.time() - self._last_obs_packet_at
                    if silence_duration > 3.5:
                        logger.warning(
                            f"[RTMP Watchdog] OBS silent for {silence_duration:.1f}s. Switching to Standby Fallback."
                        )
                        self._set_obs_connected_unlocked(False)

    def get_interface_description(self) -> str:
        return """
        RTMP Engine Tool (rtmp_engine):
        - PURPOSE: Standalone RTMP Ingress Server (Port 1935) + FFmpeg Passthrough Relays.
        - CAPABILITIES:
            - is_ffmpeg_available() -> bool: Returns True if FFmpeg binary is installed.
            - is_obs_connected() -> bool: Returns True if OBS is currently streaming to server.
            - set_obs_connected(connected, stream_key, flv_header) -> None: Updates OBS connection state.
            - set_fallback_config(config) -> None: Configure standby mode assets.
            - cache_metadata(chunk) -> None: Cache FLV metadata tag.
            - cache_avc_config(chunk) -> None: Cache H.264 sequence header.
            - cache_aac_config(chunk) -> None: Cache AAC sequence header.
            - start_relay(dest_id, rtmp_url, stream_key, platform) -> dict: Spawns relay.
            - stop_relay(dest_id) -> bool: Terminates relay process.
            - stop_all_relays() -> int: Terminates all active relay processes.
            - get_relay_status(dest_id) -> dict: Get current status of a specific relay.
            - get_all_relays() -> dict: Get status of all relays.
            - get_source_status() -> dict: Current source: obs, fallback, or waiting.
            - broadcast_flv_bytes(chunk, from_obs=False) -> None: Forward FLV bytes to all relays.
            - close() -> None: Gracefully close RTMP server and relays.
        """

    def is_ffmpeg_available(self) -> bool:
        """Check if ffmpeg is installed on the host system."""
        return self._ffmpeg_path is not None

    def set_fallback_config(self, config: dict):
        with self._lock:
            self._fallback_config.update(config)
            has_relays = bool(self._relays)
            if not self._obs_connected and has_relays:
                if self._fallback_config.get("mode") != "disabled":
                    self._fallback_generator.start(self._fallback_config)
                else:
                    self._fallback_generator.stop()

    def set_obs_connected(self, connected: bool, stream_key: str = "", flv_header: Optional[bytes] = None):
        with self._lock:
            self._set_obs_connected_unlocked(connected, stream_key, flv_header)

    def _set_obs_connected_unlocked(self, connected: bool, stream_key: str = "", flv_header: Optional[bytes] = None):
        if self._obs_connected == connected and bool(stream_key) == bool(self._obs_stream_key):
            return
        self._obs_connected = connected
        self._obs_stream_key = stream_key
        self._flv_processor.reset_source()
        if connected:
            self._last_obs_packet_at = time.time()
            if flv_header:
                self._flv_header = flv_header
            # Stop standby generator when OBS connects
            self._fallback_generator.stop()
            logger.info("[RTMP Engine] Switched live stream feed to OBS.")
        else:
            self._last_obs_packet_at = 0.0
            self._flv_header = None
            self._metadata_bytes = None
            self._avc_config_bytes = None
            self._aac_config_bytes = None
            # Start standby generator if relays are active and fallback is enabled
            if self._relays and self._fallback_config.get("mode") != "disabled":
                self._fallback_generator.start(self._fallback_config)
                logger.info("[RTMP Engine] Switched live stream feed to Standby Fallback generator.")

    def is_obs_connected(self) -> bool:
        with self._lock:
            return self._obs_connected

    def cache_metadata(self, chunk: bytes):
        with self._lock:
            self._last_obs_packet_at = time.time()
            self._metadata_bytes = chunk

    def cache_avc_config(self, chunk: bytes):
        with self._lock:
            self._last_obs_packet_at = time.time()
            self._avc_config_bytes = chunk

    def cache_aac_config(self, chunk: bytes):
        with self._lock:
            self._last_obs_packet_at = time.time()
            self._aac_config_bytes = chunk

    def broadcast_flv_bytes(self, chunk: bytes, from_obs: bool = False):
        """Write incoming stream bytes into stdin of all active FFmpeg relays with smooth timestamps."""
        with self._lock:
            # Strictly isolate feeds: drop chunks from inactive source
            if self._obs_connected and not from_obs:
                return
            if not self._obs_connected and from_obs:
                return

            if from_obs:
                self._last_obs_packet_at = time.time()

            processed_chunk = self._flv_processor.process_bytes(chunk)
            if not processed_chunk:
                return

            for dest_id, info in list(self._relays.items()):
                proc: subprocess.Popen = info.get("process")
                if proc and proc.poll() is None and proc.stdin:
                    try:
                        proc.stdin.write(processed_chunk)
                        proc.stdin.flush()
                    except Exception:
                        pass

    def start_relay(
        self,
        dest_id: str,
        rtmp_url: str,
        stream_key: str,
        ingress_url: str = "",
        platform: str = "custom"
    ) -> Dict[str, Any]:
        """
        Start an FFmpeg stream relay process reading from pipe:0 (our embedded RTMP server).
        """
        if not self._ffmpeg_path:
            return {
                "success": False,
                "dest_id": dest_id,
                "error": "FFmpeg is not installed on the system.",
                "status": "ERROR"
            }

        with self._lock:
            if dest_id in self._relays and self._relays[dest_id]["process"].poll() is None:
                self._stop_relay_unlocked(dest_id)

            if not self._relays:
                self._flv_processor.reset_source()

            target = rtmp_url.rstrip("/") + "/" + stream_key.lstrip("/")

            # Ensure fallback generator is running if OBS is not connected
            if not self._obs_connected and self._fallback_config.get("mode") != "disabled":
                if not self._fallback_generator.is_running:
                    self._fallback_generator.start(self._fallback_config)
                # Wait briefly for fallback generator to generate the initial sequence headers if not yet cached
                start_wait = time.time()
                while (not self._flv_processor.cached_avc_config or not self._flv_processor.cached_aac_config) and (time.time() - start_wait < 1.0):
                    self._lock.release()
                    time.sleep(0.05)
                    self._lock.acquire()

            # Command: Read FLV from stdin (pipe:0), pass through without re-encoding (-c copy) to target RTMP
            cmd = [
                self._ffmpeg_path,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "flv",
                "-i", "pipe:0",
                "-c", "copy",
                "-flvflags", "no_sequence_end",
                "-f", "flv",
                target
            ]

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    text=False,
                )

                # Send cached headers immediately so FFmpeg demuxer initializes cleanly
                if proc.stdin:
                    header = self._flv_header or b"FLV\x01\x05\x00\x00\x00\t\x00\x00\x00\x00"
                    proc.stdin.write(header)
                    meta = self._metadata_bytes or self._flv_processor.cached_metadata
                    if meta:
                        proc.stdin.write(meta)
                    avc = self._avc_config_bytes or self._flv_processor.cached_avc_config
                    if avc:
                        proc.stdin.write(avc)
                    aac = self._aac_config_bytes or self._flv_processor.cached_aac_config
                    if aac:
                        proc.stdin.write(aac)
                    proc.stdin.flush()

                # Release lock during verification sleep so background feed writes chunks to proc.stdin without blocking
                self._lock.release()
                time.sleep(0.3)
                self._lock.acquire()

                if proc.poll() is not None:
                    err = ""
                    try:
                        err = (proc.stderr.read() or b"").decode(errors="ignore")[-1000:]
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "dest_id": dest_id,
                        "error": err or f"FFmpeg relay exited immediately with code {proc.returncode}",
                        "status": "ERROR"
                    }

                self._relays[dest_id] = {
                    "process": proc,
                    "target_url": rtmp_url,
                    "platform": platform,
                    "started_at": time.time(),
                    "status": "STREAMING",
                    "last_error": "",
                    "pid": proc.pid
                }

                # If OBS is not connected and fallback is enabled, start fallback generator
                if not self._obs_connected and self._fallback_config.get("mode") != "disabled":
                    self._fallback_generator.start(self._fallback_config)
                
                return {
                    "success": True,
                    "dest_id": dest_id,
                    "pid": proc.pid,
                    "status": "STREAMING"
                }
            except Exception as e:
                return {
                    "success": False,
                    "dest_id": dest_id,
                    "error": str(e),
                    "status": "ERROR"
                }

    def stop_relay(self, dest_id: str) -> bool:
        """Stop a running relay process instantly by killing FFmpeg."""
        with self._lock:
            res = self._stop_relay_unlocked(dest_id)
            if not self._relays:
                self._fallback_generator.stop()
            return res

    def _stop_relay_unlocked(self, dest_id: str) -> bool:
        if dest_id not in self._relays:
            return False

        relay_info = self._relays[dest_id]
        proc: subprocess.Popen = relay_info.get("process")

        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass
                # Force kill immediately so TCP connection to YouTube/Twitch cuts off instantly
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass

        relay_info["status"] = "STOPPED"
        del self._relays[dest_id]
        return True

    def stop_all_relays(self) -> int:
        """Stop all active relay processes instantly."""
        with self._lock:
            count = 0
            dest_ids = list(self._relays.keys())
            for dest_id in dest_ids:
                if self._stop_relay_unlocked(dest_id):
                    count += 1
            self._fallback_generator.stop()
            return count

    def get_relay_status(self, dest_id: str) -> Dict[str, Any]:
        """Get the current status of a specific relay."""
        with self._lock:
            if dest_id not in self._relays:
                return {"dest_id": dest_id, "status": "STOPPED", "running": False}

            info = self._relays[dest_id]
            proc: subprocess.Popen = info["process"]
            is_running = proc.poll() is None

            return {
                "dest_id": dest_id,
                "status": info["status"] if is_running else "STOPPED",
                "running": is_running,
                "pid": info["pid"],
                "platform": info["platform"],
                "uptime_seconds": int(time.time() - info["started_at"]) if is_running else 0
            }

    def get_all_relays(self) -> Dict[str, Dict[str, Any]]:
        """Get current status of all registered relays."""
        with self._lock:
            result = {}
            for dest_id, info in list(self._relays.items()):
                proc: subprocess.Popen = info["process"]
                is_running = proc.poll() is None
                last_error = info.get("last_error", "")
                if not is_running and proc.stderr:
                    try:
                        last_error = (proc.stderr.read() or b"").decode(errors="ignore")[-1000:]
                    except Exception:
                        pass
                result[dest_id] = {
                    "dest_id": dest_id,
                    "status": info["status"] if is_running else "STOPPED",
                    "running": is_running,
                    "pid": info["pid"],
                    "platform": info["platform"],
                    "source": "obs" if self._obs_connected else ("fallback" if self._fallback_generator.is_running else "waiting"),
                    "last_error": last_error,
                    "uptime_seconds": int(time.time() - info["started_at"]) if is_running else 0
                }
            return result

    def get_source_status(self) -> Dict[str, Any]:
        with self._lock:
            fallback_running = bool(self._fallback_generator.is_running)
            return {
                "obs_connected": bool(self._obs_connected),
                "fallback_running": fallback_running,
                "active_source": "obs" if self._obs_connected else ("fallback" if fallback_running else "waiting"),
                "fallback_config": dict(self._fallback_config),
            }


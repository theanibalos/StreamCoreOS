import asyncio
import json
import os
import shutil
from typing import Optional

from microcoreos.base_tool import BaseTool
from tools.stream_tool._encoder_detector import EncoderDetector


class StreamTool(BaseTool):
    """Herramienta central de emisión/restream.

    Los plugins NO arrancan FFmpeg ni conocen detalles RTMP. Solo llaman a esta
    herramienta para iniciar/detener salidas.
    """

    # ── Identidad y Ciclo de Vida ──────────────────────────────────────────────

    def __init__(self):
        self.db = None
        self.bus = None
        self.state = None
        self.logger = None
        self.rtmp_engine = None
        self._ffmpeg_path: Optional[str] = None
        self._processes: dict[int, asyncio.subprocess.Process] = {}

    @property
    def name(self) -> str:
        return "stream_tool"

    def get_interface_description(self) -> str:
        return """
        Stream Tool (stream_tool):
        - PURPOSE: Emisión y restreaming centralizado multi-destino.
        - CAPABILITIES:
            - RTMP ingest local (rtmp_engine) + relays FFmpeg de cero latencia.
            - Fallback a FFmpeg leyendo STREAM_INPUT_URL si rtmp_engine no está activo.
            - Entrada OBS: rtmp://localhost:1935/live/{obs_stream_key}.
            - Vídeo de fallback en bucle si la fuente se desconecta.
            - Detección automática de aceleración hardware (NVENC, VAAPI, QSV, AMF, libx264).
        - PUBLIC METHODS:
            - start_output(output_id): Inicia una salida RTMP por ID.
            - stop_output(output_id): Detiene una salida RTMP por ID.
            - start_active_outputs(): Inicia todas las salidas habilitadas.
            - stop_active_outputs(): Detiene todas las salidas activas.
            - get_runtime_status(): Estado en tiempo real de ingest, conexiones y relays.
            - get_encoders(): Encoders de hardware/software detectados y recomendación.
            - set_fallback_video(video_path): Configura el vídeo de fallback.
        """

    async def setup(self):
        self._ffmpeg_path = shutil.which("ffmpeg")

    async def on_boot_complete(self, container):
        self.db = container.get("db") if container.has_tool("db") else None
        self.bus = container.get("event_bus") if container.has_tool("event_bus") else None
        self.state = container.get("state") if container.has_tool("state") else None
        self.logger = container.get("logger") if container.has_tool("logger") else None
        self.rtmp_engine = container.get("rtmp_engine") if container.has_tool("rtmp_engine") else None
        if self.rtmp_engine and os.path.exists("media/fallback.mp4"):
            self.rtmp_engine.set_fallback_config({"mode": "video", "video_path": "media/fallback.mp4"})
        if self.db:
            # Relays/FFmpeg processes do not survive backend restart. Avoid stale
            # UI states saying "live" when nothing is actually running.
            await self.db.execute(
                "UPDATE stream_outputs SET status='stopped', updated_at=datetime('now') WHERE status != 'stopped'"
            )

    async def shutdown(self):
        for output_id in list(self._processes.keys()):
            try:
                await self.stop_output(output_id)
            except Exception:
                pass

    # ── API Pública: Gestión de Salidas (Restream) ─────────────────────────────

    async def start_output(self, output_id: int) -> dict:
        self._check_ready()
        row = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
        if not row:
            raise ValueError("Stream output not found")

        rtmp_url = (row.get("rtmp_url") or "").strip()
        stream_key = (row.get("stream_key_secret") or "").strip()
        if not rtmp_url:
            raise ValueError(f"El destino '{row.get('name') or output_id}' no tiene RTMP URL configurada.")
        if not stream_key:
            raise ValueError(f"El destino '{row.get('name') or output_id}' no tiene Stream Key configurada.")

        # Si ya hay proceso vivo, solo devuelve estado actual.
        proc = self._processes.get(output_id)
        if proc and proc.returncode is None:
            await self.db.execute(
                "UPDATE stream_outputs SET status='live', enabled=1, updated_at=datetime('now') WHERE id=$1",
                [output_id],
            )
            updated = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
            return self._serialize(updated)

        input_url = await self._get_obs_input_url()

        # ── DIRECT HIGH-PERFORMANCE RELAY (0% CPU/GPU, ZERO LAG) ───────────
        if self.rtmp_engine:
            result = self.rtmp_engine.start_relay(
                str(output_id),
                rtmp_url,
                stream_key,
                platform=row.get("platform") or "custom",
            )
            if not result.get("success"):
                raise RuntimeError(result.get("error") or "No se pudo iniciar el relay RTMP")
        else:
            if not self._ffmpeg_path:
                raise RuntimeError("ffmpeg no está instalado o no está en PATH")
            target_url = self._target_url(row)
            cmd = [
                self._ffmpeg_path,
                "-hide_banner",
                "-loglevel", "warning",
                "-re",
                "-i", input_url,
                "-c", "copy",
                "-flvflags", "no_sequence_end",
                "-f", "flv",
                target_url,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            self._processes[output_id] = proc

        await self.db.execute(
            "UPDATE stream_outputs SET status='live', enabled=1, updated_at=datetime('now') WHERE id=$1",
            [output_id],
        )
        updated = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
        data = self._serialize(updated)
        if self.bus:
            await self.bus.publish("stream.output.started", {**data, "input_url": input_url})
        return data

    async def stop_output(self, output_id: int) -> dict:
        self._check_ready()
        row = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
        if not row:
            raise ValueError("Stream output not found")

        if self.rtmp_engine:
            self.rtmp_engine.stop_relay(str(output_id))

        proc = self._processes.pop(output_id, None)
        if proc:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

        await self.db.execute(
            "UPDATE stream_outputs SET status='stopped', updated_at=datetime('now') WHERE id=$1",
            [output_id],
        )
        updated = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [output_id])
        data = self._serialize(updated)
        if self.bus:
            await self.bus.publish("stream.output.stopped", data)
        return data

    async def start_active_outputs(self) -> list[dict]:
        self._check_ready()
        rows = await self.db.query("SELECT id FROM stream_outputs WHERE enabled=1 ORDER BY created_at ASC, id ASC")
        if not rows:
            return []
        results = []
        errors = []
        for row in rows:
            try:
                res = await self.start_output(row["id"])
                results.append(res)
            except Exception as e:
                err_msg = str(e)
                if self.logger:
                    self.logger.error(f"[StreamTool] Error iniciando salida {row['id']}: {err_msg}")
                errors.append(err_msg)
                await self.db.execute(
                    "UPDATE stream_outputs SET status='error', updated_at=datetime('now') WHERE id=$1",
                    [row["id"]],
                )
                updated = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [row["id"]])
                if updated:
                    results.append(self._serialize(updated))

        # If no output started successfully and there were errors, raise exception
        if not any(r.get("status") == "live" for r in results) and errors:
            raise RuntimeError("; ".join(errors))
        return results

    async def stop_active_outputs(self) -> list[dict]:
        self._check_ready()
        rows = await self.db.query("SELECT id FROM stream_outputs WHERE status != 'stopped' OR enabled=1 ORDER BY created_at ASC, id ASC")
        results = []
        for row in rows:
            try:
                res = await self.stop_output(row["id"])
                results.append(res)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[StreamTool] Error deteniendo salida {row['id']}: {e}")
                await self.db.execute(
                    "UPDATE stream_outputs SET status='stopped', updated_at=datetime('now') WHERE id=$1",
                    [row["id"]],
                )
                updated = await self.db.query_one("SELECT * FROM stream_outputs WHERE id=$1", [row["id"]])
                if updated:
                    results.append(self._serialize(updated))
        return results

    # ── API Pública: Estado, Encoders y Fallback ───────────────────────────────

    def get_encoders(self) -> dict:
        """Returns detected hardware encoders and recommended default."""
        available, recommended = EncoderDetector.detect()
        return {"available": available, "recommended": recommended}

    async def set_fallback_video(self, video_path: str) -> dict:
        config = {"mode": "video", "video_path": video_path}
        if self.rtmp_engine:
            self.rtmp_engine.set_fallback_config(config)
        if self.bus:
            await self.bus.publish("stream.fallback.updated", config)
        return config

    async def get_runtime_status(self) -> dict:
        input_url = await self._get_obs_input_url()
        relays = {}
        source_status = {"obs_connected": False, "fallback_running": False, "active_source": "waiting", "fallback_config": {}}
        obs_connected = False
        ffmpeg_available = self._ffmpeg_path is not None
        if self.rtmp_engine:
            source_status = self.rtmp_engine.get_source_status()
            obs_connected = bool(source_status.get("obs_connected"))
            ffmpeg_available = bool(self.rtmp_engine.is_ffmpeg_available())
            relays = self.rtmp_engine.get_all_relays()

        live_rows = []
        enabled_rows = []
        if self.db:
            live_rows = await self.db.query("SELECT id FROM stream_outputs WHERE status='live'")
            enabled_rows = await self.db.query("SELECT id FROM stream_outputs WHERE enabled=1")
            if self.rtmp_engine:
                for row in live_rows:
                    relay = (relays or {}).get(str(row["id"]))
                    if not relay or not relay.get("running"):
                        await self.db.execute(
                            "UPDATE stream_outputs SET status='error', updated_at=datetime('now') WHERE id=$1",
                            [row["id"]],
                        )
                live_rows = await self.db.query("SELECT id FROM stream_outputs WHERE status='live'")
        
        is_transmitting = len(live_rows) > 0 or len(relays or {}) > 0
        return {
            "input_url": input_url,
            "obs_url": "rtmp://localhost:1935/live",
            "obs_stream_key": input_url.rsplit("/", 1)[-1] if "/" in input_url else "streamcore",
            "obs_connected": obs_connected,
            "ffmpeg_available": ffmpeg_available,
            "rtmp_engine_available": bool(self.rtmp_engine),
            "relays": relays,
            "relays_count": len(relays or {}),
            "live_outputs_count": len(live_rows),
            "enabled_outputs_count": len(enabled_rows),
            "is_transmitting": is_transmitting,
            "active_source": source_status.get("active_source", "waiting") if is_transmitting or obs_connected else "waiting",
            "fallback_running": bool(source_status.get("fallback_running")),
            "fallback_mode": "video" if os.path.exists("media/fallback.mp4") else "image_audio",
            "fallback_video_configured": os.path.exists("media/fallback.mp4"),
            "fallback_video_path": "media/fallback.mp4" if os.path.exists("media/fallback.mp4") else None,
            "fallback_video_url": "/api/stream-media/fallback.mp4" if os.path.exists("media/fallback.mp4") else None,
        }

    # ── Métodos Auxiliares Internos ───────────────────────────────────────────

    def _check_ready(self):
        if not self.db:
            raise RuntimeError("StreamTool necesita la herramienta db")

    def _serialize(self, row: dict) -> dict:
        secret = row.get("stream_key_secret") or ""
        settings = json.loads(row.get("settings") or "{}")
        return {
            "id": row["id"],
            "name": row["name"],
            "platform": row["platform"],
            "channel_id": row["channel_id"],
            "enabled": bool(row["enabled"]),
            "overlay_id": row.get("overlay_id"),
            "rtmp_url": row.get("rtmp_url"),
            "stream_key_configured": bool(secret),
            "stream_key_preview": secret[-4:] if secret else None,
            "status": row["status"],
            "settings": settings,
            "encoder": settings.get("encoder", "auto"),
            "bitrate_kbps": settings.get("bitrate_kbps", 6000),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def _get_obs_input_url(self) -> str:
        configured = os.getenv("STREAM_INPUT_URL")
        if configured:
            return configured
        key = "streamcore"
        if self.state:
            key = await self.state.get("obs_stream_key", default="streamcore", namespace="config")
        return f"rtmp://127.0.0.1:1935/live/{key}"

    def _target_url(self, row: dict) -> str:
        rtmp_url = (row.get("rtmp_url") or "").strip()
        stream_key = (row.get("stream_key_secret") or "").strip()
        if not rtmp_url:
            raise ValueError("Este destino no tiene RTMP URL configurada")
        if not stream_key:
            raise ValueError("Este destino no tiene stream key configurada")
        return f"{rtmp_url.rstrip('/')}/{stream_key}"

import asyncio
import logging
import socket
from typing import Optional

# pyrtmp.rtmp has an import-time side effect: it configures the Python root
# logger at DEBUG. Preserve the app logging state so importing this RTMP tool
# does not make httpcore/httpx/aiosqlite/websockets spam DEBUG logs.
_root_logger = logging.getLogger()
_prev_root_level = _root_logger.level
_prev_handlers = list(_root_logger.handlers)

from pyrtmp.rtmp import (  # noqa: E402
    SimpleRTMPController,
    RTMPProtocol,
    SessionManager,
    NCConnect,
    WindowAcknowledgementSize,
    NCCreateStream,
    NSPublish,
    MetaDataMessage,
    SetChunkSize,
    VideoMessage,
    AudioMessage,
    NSCloseStream,
    NSDeleteStream,
    StreamClosedException,
    MessageFactory
)
from pyrtmp.flv import FLVWriter, FLVMediaType  # noqa: E402

_root_logger.setLevel(_prev_root_level)
if not _prev_handlers:
    for handler in list(_root_logger.handlers):
        _root_logger.removeHandler(handler)

logger = logging.getLogger("StreamOS.RtmpServer")


class StreamOSRTMPController(SimpleRTMPController):
    def __init__(self, engine_tool):
        super().__init__()
        self.engine_tool = engine_tool
        self.flv_writer = FLVWriter()
        self.is_publishing = False

    async def client_callback(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = SessionManager(reader=reader, writer=writer)
        logger.debug(f"[RTMP Server] Client connected from {session.peername}")

        # Enable TCP keepalive on accepted socket to detect silent disconnects
        try:
            sock = writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                if hasattr(socket, "TCP_KEEPIDLE"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 3)
                if hasattr(socket, "TCP_KEEPINTVL"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
                if hasattr(socket, "TCP_KEEPCNT"):
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        except Exception:
            pass

        try:
            await self.on_handshake(session)
            logger.debug(f"[RTMP Server] Handshake completed with {session.peername}")

            chunk_iterator = session.read_chunks_from_stream()
            while True:
                # 3.5s read timeout while streaming, 15s during handshake/idle
                timeout = 3.5 if self.is_publishing else 15.0
                try:
                    chunk = await asyncio.wait_for(chunk_iterator.__anext__(), timeout=timeout)
                except StopAsyncIteration:
                    break
                except (asyncio.TimeoutError, TimeoutError):
                    if self.is_publishing:
                        logger.warning(
                            f"[RTMP Server] Ingress timeout ({timeout}s without data) from {session.peername}. Closing dead session."
                        )
                    break

                message = MessageFactory.from_chunk(chunk)
                if isinstance(message, NCConnect):
                    await self.on_nc_connect(session, message)
                elif isinstance(message, WindowAcknowledgementSize):
                    await self.on_window_acknowledgement_size(session, message)
                elif isinstance(message, NCCreateStream):
                    await self.on_nc_create_stream(session, message)
                elif isinstance(message, NSPublish):
                    await self.on_ns_publish(session, message)
                elif isinstance(message, MetaDataMessage):
                    await self.on_metadata(session, message)
                elif isinstance(message, SetChunkSize):
                    await self.on_set_chunk_size(session, message)
                elif isinstance(message, VideoMessage):
                    await self.on_video_message(session, message)
                elif isinstance(message, AudioMessage):
                    await self.on_audio_message(session, message)
                elif isinstance(message, NSCloseStream):
                    await self.on_ns_close_stream(session, message)
                elif isinstance(message, NSDeleteStream):
                    await self.on_ns_delete_stream(session, message)
                else:
                    await self.on_unknown_message(session, message)

        except StreamClosedException as ex:
            logger.debug(f"[RTMP Server] Client disconnected {session.peername}")
            await self.on_stream_closed(session, ex)
        except Exception as ex:
            logger.warning(f"[RTMP Server] Connection error with {session.peername}: {ex}")
        finally:
            await self.cleanup(session)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def on_ns_publish(self, session: SessionManager, message: NSPublish) -> None:
        await super().on_ns_publish(session, message)
        self.is_publishing = True
        logger.info(f"[RTMP Server] OBS Client publishing stream: '{message.publishing_name}'")
        
        # Generate and cache FLV Header
        header_bytes = self.flv_writer.write_header()
        self.engine_tool.set_obs_connected(True, message.publishing_name, header_bytes)

    async def on_metadata(self, session: SessionManager, message: MetaDataMessage) -> None:
        await super().on_metadata(session, message)
        if self.is_publishing:
            try:
                raw = message.to_raw_meta()
                tag_bytes = self.flv_writer.write(message.timestamp, raw, FLVMediaType.OBJECT)
                self.engine_tool.cache_metadata(tag_bytes)
                self.engine_tool.broadcast_flv_bytes(tag_bytes, from_obs=True)
            except Exception as e:
                logger.warning(f"Error encoding metadata: {e}")

    async def on_video_message(self, session: SessionManager, message: VideoMessage) -> None:
        await super().on_video_message(session, message)
        if self.is_publishing:
            try:
                tag_bytes = self.flv_writer.write(message.timestamp, message.payload, FLVMediaType.VIDEO)
                
                # Check if this is the H.264 / AVC Sequence Header (AVCDecoderConfigurationRecord)
                if len(message.payload) >= 2:
                    codec_id = message.payload[0] & 0x0F
                    avc_packet_type = message.payload[1]
                    if codec_id == 7 and avc_packet_type == 0:
                        logger.info("[RTMP Server] Captured H.264 AVC Sequence Header (SPS/PPS)")
                        self.engine_tool.cache_avc_config(tag_bytes)
                        
                self.engine_tool.broadcast_flv_bytes(tag_bytes, from_obs=True)
            except Exception as e:
                logger.warning(f"Error encoding video frame: {e}")

    async def on_audio_message(self, session: SessionManager, message: AudioMessage) -> None:
        await super().on_audio_message(session, message)
        if self.is_publishing:
            try:
                tag_bytes = self.flv_writer.write(message.timestamp, message.payload, FLVMediaType.AUDIO)
                
                # Check if this is the AAC Sequence Header (AudioSpecificConfig)
                if len(message.payload) >= 2:
                    sound_format = (message.payload[0] >> 4) & 0x0F
                    aac_packet_type = message.payload[1]
                    if sound_format == 10 and aac_packet_type == 0:
                        logger.info("[RTMP Server] Captured AAC Audio Sequence Header")
                        self.engine_tool.cache_aac_config(tag_bytes)

                self.engine_tool.broadcast_flv_bytes(tag_bytes, from_obs=True)
            except Exception as e:
                logger.warning(f"Error encoding audio frame: {e}")

    async def on_stream_closed(self, session: SessionManager, exception: StreamClosedException) -> None:
        await super().on_stream_closed(session, exception)
        if self.is_publishing:
            self.is_publishing = False
            self.engine_tool.set_obs_connected(False)
            logger.info("[RTMP Server] OBS Client disconnected.")

    async def cleanup(self, session: SessionManager) -> None:
        await super().cleanup(session)
        if self.is_publishing:
            self.is_publishing = False
            self.engine_tool.set_obs_connected(False)
            logger.info("[RTMP Server] Session cleaned up -> Standby fallback activated.")


class RtmpIngressServer:
    def __init__(self, engine_tool, host: str = "0.0.0.0", port: int = 1935):
        self.engine_tool = engine_tool
        self.host = host
        self.port = port
        self.server: Optional[asyncio.Server] = None
        self.is_running = False

    async def start(self):
        loop = asyncio.get_event_loop()
        try:
            self.server = await loop.create_server(
                lambda: RTMPProtocol(controller=StreamOSRTMPController(self.engine_tool)),
                host=self.host,
                port=self.port,
            )
            self.is_running = True
            print(f"[RTMP Server] 📡 Listening for OBS streams on rtmp://{self.host}:{self.port}/live")
        except Exception as e:
            print(f"[RTMP Server] ⚠️  Could not bind RTMP port {self.port}: {e}")
            self.is_running = False

    async def stop(self):
        if self.server:
            try:
                self.server.close()
                await asyncio.wait_for(self.server.wait_closed(), timeout=0.3)
            except Exception:
                pass
            self.server = None
            self.is_running = False


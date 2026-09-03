"""
Plugin-facing transport types for the HTTP Server Tool.

Everything an http tool hands to a plugin has to be a type the CONTRACT owns.
Handing over the web framework's own objects makes the framework part of the
contract: a replacement implementation would have to reproduce Starlette's
UploadFile and WebSocket to stay compatible, which is not a contract anybody
can meet. These two wrappers are the boundary.
"""

from typing import Any, Optional


class UploadedFile:
    """
    One file from a multipart request, as plugins see it in data["_files"].

    `stream` is a file-like object with the sync read/seek interface that
    storage clients expect (boto3's upload_fileobj among them), which is why
    it is exposed alongside the async read().
    """

    __slots__ = ("filename", "content_type", "stream", "_raw")

    def __init__(self, filename: Optional[str], content_type: Optional[str], stream: Any, raw: Any = None):
        self.filename = filename
        self.content_type = content_type
        self.stream = stream
        self._raw = raw

    @classmethod
    def from_starlette(cls, upload) -> "UploadedFile":
        return cls(
            filename=upload.filename,
            content_type=upload.content_type,
            stream=upload.file,
            raw=upload,
        )

    @property
    def file(self) -> Any:
        """Alias for `stream`. boto3's upload_fileobj and friends expect `.file`."""
        return self.stream

    async def read(self, size: int = -1) -> bytes:
        """Read the file's bytes. Beware size: this loads them into memory."""
        if hasattr(self._raw, "read"):
            return await self._raw.read(size)
        return self.stream.read(size)

    async def seek(self, offset: int) -> None:
        if hasattr(self._raw, "seek"):
            await self._raw.seek(offset)
        else:
            self.stream.seek(offset)

    def __repr__(self) -> str:
        return f"UploadedFile(filename={self.filename!r}, content_type={self.content_type!r})"


class WebSocketConnection:
    """
    One WebSocket connection, as plugins see it in on_connect/on_disconnect.

    The surface is deliberately the four operations a handler needs. Anything
    an implementation cannot provide should fail here, at the boundary, rather
    than by a plugin reaching into a framework object that a replacement does
    not have.
    """

    __slots__ = ("_ws",)

    def __init__(self, ws):
        self._ws = ws

    async def send_text(self, data: str) -> None:
        await self._ws.send_text(data)

    async def send_json(self, data: Any) -> None:
        await self._ws.send_json(data)

    async def receive_text(self) -> str:
        return await self._ws.receive_text()

    async def receive_json(self) -> Any:
        return await self._ws.receive_json()

    async def close(self, code: int = 1000) -> None:
        await self._ws.close(code=code)

    @property
    def query_params(self) -> dict:
        return dict(self._ws.query_params)

    @property
    def path_params(self) -> dict:
        return dict(self._ws.path_params)

    def __repr__(self) -> str:
        return f"WebSocketConnection(path={getattr(self._ws, 'url', None)})"

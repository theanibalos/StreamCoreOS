import importlib
import json
import os
import secrets
import sys
import time
from urllib.parse import urlencode

import grpc
import httpx
from google.protobuf.json_format import MessageToDict
from grpc_tools import protoc
import grpc_tools

from microcoreos.base_tool import BaseTool


_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeTool(BaseTool):
    """YouTube Live wrapper: OAuth + YouTube Data API live chat helpers."""

    @property
    def name(self) -> str:
        return "youtube"

    def __init__(self) -> None:
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._redirect_uri: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._pending_states: set[str] = set()
        self._scopes: list[str] = ["https://www.googleapis.com/auth/youtube.readonly"]
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0
        self._channel_id: str | None = None
        self._channel_title: str | None = None
        self._available = False
        self._grpc_modules = None
        self.on_token_refreshed = None

    async def setup(self) -> None:
        self._client_id = os.getenv("YOUTUBE_CLIENT_ID")
        self._client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
        self._redirect_uri = os.getenv(
            "YOUTUBE_REDIRECT_URI", "http://localhost/api/auth/youtube/callback"
        )
        if not self._client_id or not self._client_secret:
            print("[YouTubeTool] ⚠️  YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET not set. Tool unavailable.")
            return
        self._client = httpx.AsyncClient(timeout=30)
        self._available = True
        print("[YouTubeTool] Ready.")

    async def shutdown(self) -> None:
        if self._client:
            await self._client.aclose()
        print("[YouTubeTool] Shutdown complete.")

    async def on_boot_complete(self, container) -> None:
        if self._available and not self._access_token:
            url, _ = self.get_auth_url()
            print(f"\n{'='*60}\n[YouTubeTool] No active session — authentication optional.\nOpen this URL to authorize YouTube:\n{url}\n{'='*60}\n")

    def require_scopes(self, scopes: list[str]) -> None:
        self._check_available()
        for scope in scopes:
            if scope not in self._scopes:
                self._scopes.append(scope)

    def get_required_scopes(self) -> list[str]:
        return list(self._scopes)

    def get_auth_url(self) -> tuple[str, str]:
        self._check_available()
        state = secrets.token_urlsafe(16)
        self._pending_states.add(state)
        query = urlencode({
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        })
        return f"{_AUTH_URL}?{query}", state

    def consume_state(self, state: str) -> bool:
        if state in self._pending_states:
            self._pending_states.discard(state)
            return True
        return False

    async def exchange_code(self, code: str) -> dict:
        self._check_available()
        resp = await self._client.post(_TOKEN_URL, data={
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri,
        })
        resp.raise_for_status()
        return resp.json()

    async def refresh_user_token(self, refresh_token: str) -> dict:
        self._check_available()
        resp = await self._client.post(_TOKEN_URL, data={
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        return resp.json()

    async def connect(self, access_token: str, refresh_token: str | None, channel_id: str, channel_title: str, expires_in: int = 3600) -> None:
        self._check_available()
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._channel_id = channel_id
        self._channel_title = channel_title
        self._expires_at = time.time() + max(60, expires_in - 60)

    async def disconnect(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0
        self._channel_id = None
        self._channel_title = None

    def get_session(self) -> dict | None:
        if not self._access_token:
            return None
        return {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "channel_id": self._channel_id,
            "channel_title": self._channel_title,
        }

    def is_connected(self) -> bool:
        return bool(self._access_token)

    async def _ensure_token(self) -> str:
        if not self._access_token:
            raise RuntimeError("No active YouTube session")
        if self._refresh_token and time.time() >= self._expires_at:
            tokens = await self.refresh_user_token(self._refresh_token)
            self._access_token = tokens["access_token"]
            if tokens.get("refresh_token"):
                self._refresh_token = tokens["refresh_token"]
            self._expires_at = time.time() + max(60, int(tokens.get("expires_in", 3600)) - 60)
            if self.on_token_refreshed:
                await self.on_token_refreshed({
                    "access_token": self._access_token,
                    "refresh_token": self._refresh_token,
                    "expires_in": int(tokens.get("expires_in", 3600)),
                    "scope": tokens.get("scope", ""),
                })
        return self._access_token

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        self._check_available()
        token = await self._ensure_token()
        resp = await self._client.get(f"{_API_BASE}{endpoint}", params=params or {}, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 401 and self._refresh_token:
            self._expires_at = 0
            token = await self._ensure_token()
            resp = await self._client.get(f"{_API_BASE}{endpoint}", params=params or {}, headers={"Authorization": f"Bearer {token}"})
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"YouTube GET {endpoint} failed {resp.status_code}: {resp.text[:1000]}") from e
        return resp.json() if resp.content else {}

    async def post(self, endpoint: str, body: dict | None = None, params: dict | None = None) -> dict:
        self._check_available()
        token = await self._ensure_token()
        resp = await self._client.post(f"{_API_BASE}{endpoint}", params=params or {}, json=body or {}, headers={"Authorization": f"Bearer {token}"})
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"YouTube POST {endpoint} failed {resp.status_code}: {resp.text[:1000]}") from e
        return resp.json() if resp.content else {}

    async def delete(self, endpoint: str, params: dict | None = None) -> dict:
        self._check_available()
        token = await self._ensure_token()
        resp = await self._client.delete(f"{_API_BASE}{endpoint}", params=params or {}, headers={"Authorization": f"Bearer {token}"})
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"YouTube DELETE {endpoint} failed {resp.status_code}: {resp.text[:1000]}") from e
        return resp.json() if resp.content else {}

    async def get_user_info(self) -> dict:
        data = await self.get("/channels", {"part": "snippet", "mine": "true"})
        items = data.get("items", [])
        if not items:
            raise RuntimeError("Authenticated Google account has no YouTube channel")
        item = items[0]
        return {"id": item["id"], "title": item.get("snippet", {}).get("title", item["id"])}

    async def get_active_broadcast(self) -> dict | None:
        # YouTube requires EXACTLY ONE filter among broadcastStatus/id/mine.
        # For the active live, use broadcastStatus=active; don't combine it with mine=true.
        data = await self.get("/liveBroadcasts", {
            "part": "id,snippet,contentDetails,status",
            "broadcastStatus": "active",
            "broadcastType": "all",
            "maxResults": 1,
        })
        items = data.get("items", [])
        if items:
            return items[0]

        # Fallback: fetch own broadcasts and filter client-side by lifecycle status.
        data = await self.get("/liveBroadcasts", {
            "part": "id,snippet,contentDetails,status",
            "mine": "true",
            "broadcastType": "all",
            "maxResults": 10,
        })
        for item in data.get("items", []):
            status = (item.get("status", {}) or {}).get("lifeCycleStatus", "")
            if status in ("live", "testing"):
                return item
        return None

    async def get_live_chat_id(self) -> str | None:
        b = await self.get_active_broadcast()
        if not b:
            return None
        return b.get("snippet", {}).get("liveChatId")

    async def list_chat_messages(self, live_chat_id: str, page_token: str | None = None, max_results: int = 500) -> dict:
        params = {
            "liveChatId": live_chat_id,
            "part": "id,snippet,authorDetails",
            "maxResults": max(200, min(2000, max_results)),
        }
        if page_token:
            params["pageToken"] = page_token
        return await self.get("/liveChat/messages", params)

    def _load_grpc_stream_modules(self):
        if self._grpc_modules:
            return self._grpc_modules

        base_dir = os.path.dirname(__file__)
        proto_path = os.path.join(base_dir, "stream_list.proto")
        gen_dir = os.path.join(base_dir, "_generated")
        os.makedirs(gen_dir, exist_ok=True)
        init_path = os.path.join(gen_dir, "__init__.py")
        if not os.path.exists(init_path):
            open(init_path, "w").close()

        pb2_path = os.path.join(gen_dir, "stream_list_pb2.py")
        pb2_grpc_path = os.path.join(gen_dir, "stream_list_pb2_grpc.py")
        if not os.path.exists(pb2_path) or not os.path.exists(pb2_grpc_path):
            proto_include = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")
            res = protoc.main([
                "grpc_tools.protoc",
                f"-I{base_dir}",
                f"-I{proto_include}",
                f"--python_out={gen_dir}",
                f"--grpc_python_out={gen_dir}",
                proto_path,
            ])
            if res != 0:
                raise RuntimeError(f"Could not generate YouTube streamList gRPC client (protoc exit {res})")

        if gen_dir not in sys.path:
            sys.path.insert(0, gen_dir)
        pb2 = importlib.import_module("stream_list_pb2")
        pb2_grpc = importlib.import_module("stream_list_pb2_grpc")
        self._grpc_modules = (pb2, pb2_grpc)
        return self._grpc_modules

    async def stream_chat_messages(self, live_chat_id: str, page_token: str | None = None, max_results: int = 500):
        """Recommended YouTube live chat streamList client using gRPC.

        Yields dicts shaped like REST liveChatMessages.list responses.
        """
        self._check_available()
        token = await self._ensure_token()
        pb2, pb2_grpc = self._load_grpc_stream_modules()

        creds = grpc.ssl_channel_credentials()
        async with grpc.aio.secure_channel("youtube.googleapis.com:443", creds) as channel:
            stub = pb2_grpc.V3DataLiveChatMessageServiceStub(channel)
            metadata = (("authorization", f"Bearer {token}"),)
            while True:
                request = pb2.LiveChatMessageListRequest(
                    live_chat_id=live_chat_id,
                    max_results=max(200, min(2000, max_results)),
                    page_token=page_token or "",
                )
                request.part.extend(["id", "snippet", "authorDetails"])
                try:
                    async for response in stub.StreamList(request, metadata=metadata):
                        data = MessageToDict(
                            response,
                            preserving_proto_field_name=False,
                            always_print_fields_with_no_presence=False,
                        )
                        page_token = data.get("nextPageToken") or page_token
                        yield data
                    return
                except grpc.aio.AioRpcError as e:
                    detail = e.details() or ""
                    code = e.code().name if e.code() else "UNKNOWN"
                    raise RuntimeError(f"YouTube gRPC streamList failed {code}: {detail}") from e

    async def send_message(self, live_chat_id: str, text: str) -> dict:
        self.require_scopes(["https://www.googleapis.com/auth/youtube.force-ssl"])
        return await self.post("/liveChat/messages", body={
            "snippet": {
                "liveChatId": live_chat_id,
                "type": "textMessageEvent",
                "textMessageDetails": {"messageText": text},
            }
        }, params={"part": "snippet"})

    async def delete_message(self, message_id: str) -> dict:
        return await self.delete("/liveChat/messages", {"id": message_id})

    async def ban_user(self, live_chat_id: str, channel_id: str, duration_s: int | None = None) -> dict:
        snippet = {
            "liveChatId": live_chat_id,
            "type": "temporary" if duration_s else "permanent",
            "bannedUserDetails": {"channelId": channel_id},
        }
        if duration_s:
            snippet["banDurationSeconds"] = duration_s
        return await self.post("/liveChat/bans", body={"snippet": snippet}, params={"part": "snippet"})

    def _check_available(self) -> None:
        if not self._available:
            raise RuntimeError("YouTubeTool not available. Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET.")

    def get_interface_description(self) -> str:
        return """
        YouTube Tool (youtube):
        - PURPOSE: Integration with YouTube Data API v3, Google OAuth 2.0, and high-performance Live Chat via gRPC streamList.
        - ENVIRONMENT:
            - YOUTUBE_CLIENT_ID: Google Cloud OAuth 2.0 Client ID.
            - YOUTUBE_CLIENT_SECRET: Google Cloud OAuth 2.0 Client Secret.
            - YOUTUBE_REDIRECT_URI: OAuth redirect callback URL (default: http://localhost/api/auth/youtube/callback).
        - OAUTH & SESSION LIFECYCLE:
            - require_scopes(scopes: list[str]) -> None: Declare needed OAuth scopes in on_boot().
            - get_required_scopes() -> list[str]: Returns list of accumulated required scopes.
            - get_auth_url() -> tuple[str, str]: Returns (authorization_url, csrf_state_token).
            - consume_state(state: str) -> bool: Validates and consumes CSRF state from callback.
            - await exchange_code(code: str) -> dict: Exchanges authorization code for tokens dictionary.
            - await refresh_user_token(refresh_token: str) -> dict: Refreshes access token with Google OAuth.
            - await connect(access_token, refresh_token, channel_id, channel_title, expires_in=3600) -> None:
                Establishes the active authenticated session.
            - await disconnect() -> None: Clears active session.
            - get_session() -> dict | None: Returns active session metadata (tokens, channel_id, channel_title).
            - is_connected() -> bool: Returns True if active session is connected and authenticated.
        - BROADCAST & CHAT DISCOVERY:
            - await get_user_info() -> dict: Returns channel details: {"id": channel_id, "title": channel_title}.
            - await get_active_broadcast() -> dict | None: Fetches currently active or testing live broadcast.
            - await get_live_chat_id() -> str | None: Extracts active liveChatId for the current live stream.
        - LIVE CHAT & MODERATION:
            - await stream_chat_messages(live_chat_id: str, page_token: str | None = None, max_results: int = 500):
                Async generator yielding real-time chat messages via gRPC streamList (recommended).
            - await list_chat_messages(live_chat_id: str, page_token: str | None = None, max_results: int = 500) -> dict:
                REST polling fallback for live chat messages.
            - await send_message(live_chat_id: str, text: str) -> dict:
                Sends a chat message to YouTube Live Chat (requires 'https://www.googleapis.com/auth/youtube.force-ssl').
            - await delete_message(message_id: str) -> dict: Deletes a chat message by message_id.
            - await ban_user(live_chat_id: str, channel_id: str, duration_s: int | None = None) -> dict:
                Bans (permanent) or timeouts (temporary, duration_s) a channel in live chat.
        - RAW HTTP GATEWAY (with auto-refreshing Bearer token):
            - await get(endpoint: str, params: dict | None = None) -> dict
            - await post(endpoint: str, body: dict | None = None, params: dict | None = None) -> dict
            - await delete(endpoint: str, params: dict | None = None) -> dict
        """

from typing import Any, Optional
from microcoreos.base_tool import BaseTool


class HttpClientTool(BaseTool):
    """
    Async HTTP Client Tool — outgoing HTTP requests via httpx.
    Plugins receive this as 'http_client' via DI.
    """

    @property
    def name(self) -> str:
        return "http_client"

    async def setup(self) -> None:
        try:
            import httpx
            self._httpx = httpx
            self._client = httpx.AsyncClient()
            print("[HttpClient] Ready.")
        except ImportError:
            raise RuntimeError("[HttpClient] httpx is required. Install with: uv add httpx")

    async def shutdown(self) -> None:
        await self._client.aclose()
        print("[HttpClient] Client closed.")

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> dict:
        return await self._request("GET", url, params=params, headers=headers, timeout=timeout)

    async def post(
        self,
        url: str,
        json: Optional[Any] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> dict:
        return await self._request("POST", url, json=json, data=data, headers=headers, timeout=timeout)

    async def put(
        self,
        url: str,
        json: Optional[Any] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> dict:
        return await self._request("PUT", url, json=json, data=data, headers=headers, timeout=timeout)

    async def delete(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> dict:
        return await self._request("DELETE", url, params=params, headers=headers, timeout=timeout)

    # ── Internal ────────────────────────────────────────────────────────────────

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            resp = await self._client.request(method, url, **kwargs)
            json_body = None
            try:
                json_body = resp.json()
            except Exception:
                pass
            return {
                "status": resp.status_code,
                "ok": resp.status_code < 400,
                "json": json_body,
                "text": resp.text,
                "headers": dict(resp.headers),
            }
        except self._httpx.TimeoutException as e:
            raise TimeoutError(f"[HttpClient] Request timed out: {url}") from e
        except self._httpx.RequestError as e:
            raise ConnectionError(f"[HttpClient] Connection error: {url} — {e}") from e

    # ── Interface description ───────────────────────────────────────────────────

    def get_interface_description(self) -> str:
        return """
        HTTP Client Tool (http_client):
        - PURPOSE: Make outgoing HTTP requests from plugins. Async, backed by httpx.
        - RESPONSE: All methods return a dict:
            {
              "status": int,       # HTTP status code (200, 404, ...)
              "ok": bool,          # True if status < 400
              "json": dict | None, # Parsed JSON body (None if not JSON)
              "text": str,         # Raw response body
              "headers": dict      # Response headers
            }
        - ERRORS:
            TimeoutError      — request exceeded the timeout
            ConnectionError   — could not reach the server
        - CAPABILITIES:
            - await get(url, params?, headers?, timeout=10) -> dict
            - await post(url, json?, data?, headers?, timeout=10) -> dict
            - await put(url, json?, data?, headers?, timeout=10) -> dict
            - await delete(url, params?, headers?, timeout=10) -> dict
        - USAGE IN PLUGIN __init__: def __init__(self, http_client, ...)
        """

from typing import Optional
from pydantic import BaseModel
from core.base_plugin import BasePlugin


class ToolStatus(BaseModel):
    name: str
    status: str
    message: Optional[str] = None


class PluginStatus(BaseModel):
    name: str
    domain: Optional[str] = None
    status: str
    error: Optional[str] = None
    tools: list[str] = []


class SystemStatusData(BaseModel):
    tools: list[ToolStatus]
    plugins: list[PluginStatus]


class SystemStatusResponse(BaseModel):
    success: bool
    data: Optional[SystemStatusData] = None
    error: Optional[str] = None


class SystemStatusPlugin(BasePlugin):
    def __init__(self, http, registry):
        self.http = http
        self.registry = registry

    async def on_boot(self):
        self.http.add_endpoint(
            "/api/system/status", "GET", self.execute,
            tags=["System"],
            response_model=SystemStatusResponse
        )

    async def execute(self, data: dict, context=None):
        try:
            dump = self.registry.get_system_dump()

            tools = [
                ToolStatus(name=name, **info).model_dump()
                for name, info in dump.get("tools", {}).items()
            ]

            plugins = [
                PluginStatus(
                    name=name,
                    domain=info.get("domain"),
                    status=info.get("status", "UNKNOWN"),
                    error=info.get("error"),
                    tools=info.get("dependencies", [])
                ).model_dump()
                for name, info in dump.get("plugins", {}).items()
            ]

            return {"success": True, "data": {"tools": tools, "plugins": plugins}}
        except Exception as e:
            print(f"[SystemStatus] Error: {e}")
            return {"success": False, "error": "Internal error"}

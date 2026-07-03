from core.base_plugin import BasePlugin


class StreamStateRpcPlugin(BasePlugin):
    """
    RPC responder for 'stream.status.requested'.

    Any domain can call:
        status = await event_bus.request("stream.status.requested", {})
    and receive: {online, session_id, started_at, broadcaster_login}

    This avoids cross-domain imports — consumers never import stream_state directly.
    """

    def __init__(self, event_bus, state, logger):
        self.bus = event_bus
        self.state = state
        self.logger = logger

    async def on_boot(self):
        await self.bus.subscribe("stream.status.requested", self._handle)

    async def _handle(self, event) -> dict:
        return {
            "online": await self.state.get("online", default=False, namespace="stream_state"),
            "session_id": await self.state.get("session_id", namespace="stream_state"),
            "started_at": await self.state.get("started_at", namespace="stream_state"),
            "broadcaster_login": await self.state.get("broadcaster_login", namespace="stream_state"),
        }

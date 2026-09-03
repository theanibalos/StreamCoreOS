import pytest

from tools.sqlite.sqlite_tool import SqliteTool
from domains.stream_outputs.plugins.create_stream_output_plugin import CreateStreamOutputPlugin
from domains.stream_outputs.plugins.list_stream_outputs_plugin import ListStreamOutputsPlugin
from domains.stream_outputs.plugins.update_stream_output_plugin import UpdateStreamOutputPlugin
from domains.stream_outputs.plugins.delete_stream_output_plugin import DeleteStreamOutputPlugin

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeHttp:
    def __init__(self):
        self.endpoints = []

    def add_endpoint(self, path, method, handler, **kwargs):
        self.endpoints.append((path, method, handler, kwargs))


class FakeLogger:
    def __init__(self):
        self.errors = []

    def error(self, msg):
        self.errors.append(msg)


@pytest.fixture
async def db(monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", ":memory:")
    tool = SqliteTool()
    await tool.setup()
    await tool.execute(
        """
        CREATE TABLE IF NOT EXISTS stream_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            overlay_id INTEGER,
            rtmp_url TEXT,
            stream_key_secret TEXT,
            status TEXT NOT NULL DEFAULT 'stopped',
            settings TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    yield tool
    await tool.shutdown()


@pytest.fixture
async def plugins(db):
    http = FakeHttp()
    logger = FakeLogger()
    return {
        "create": CreateStreamOutputPlugin(http, db, logger),
        "list": ListStreamOutputsPlugin(http, db, logger),
        "update": UpdateStreamOutputPlugin(http, db, logger),
        "delete": DeleteStreamOutputPlugin(http, db, logger),
        "http": http,
        "logger": logger,
    }


async def test_stream_outputs_crud_and_masks_stream_key(plugins):
    created = await plugins["create"].execute({
        "name": "YouTube Principal",
        "platform": "YouTube",
        "channel_id": "UC123",
        "rtmp_url": "rtmp://a.rtmp.youtube.com/live2",
        "stream_key_secret": "abcd-efgh-1234",
        "settings": {"latency": "low"},
    })

    assert created["success"] is True
    item = created["data"]
    assert item["platform"] == "youtube"
    assert item["stream_key_configured"] is True
    assert item["stream_key_preview"] == "1234"
    assert "stream_key_secret" not in item

    listed = await plugins["list"].execute({})
    assert listed["success"] is True
    assert len(listed["data"]) == 1
    assert listed["data"][0]["settings"] == {"latency": "low"}
    assert "stream_key_secret" not in listed["data"][0]

    updated = await plugins["update"].execute({
        "id": item["id"],
        "enabled": False,
        "platform": "custom",
        "status": "ready",
        "settings": {"provider": "kick"},
    })
    assert updated["success"] is True
    assert updated["data"]["enabled"] is False
    assert updated["data"]["platform"] == "custom"
    assert updated["data"]["status"] == "ready"

    deleted = await plugins["delete"].execute({"id": item["id"]})
    assert deleted == {"success": True, "data": {"id": item["id"], "deleted": True}}

    listed_after_delete = await plugins["list"].execute({})
    assert listed_after_delete == {"success": True, "data": []}


async def test_stream_outputs_routes_are_registered(plugins):
    for key in ("create", "list", "update", "delete"):
        await plugins[key].on_boot()

    routes = {(path, method) for path, method, *_ in plugins["http"].endpoints}
    assert ("/api/stream-outputs", "POST") in routes
    assert ("/api/stream-outputs", "GET") in routes
    assert ("/api/stream-outputs/{id}", "PUT") in routes
    assert ("/api/stream-outputs/{id}", "DELETE") in routes


async def test_update_and_delete_missing_stream_output_return_404_shape(plugins):
    class Context:
        def __init__(self):
            self.status = None

        def set_status(self, status):
            self.status = status

    update_context = Context()
    updated = await plugins["update"].execute({"id": 999, "enabled": True}, update_context)
    assert updated == {"success": False, "error": "Stream output not found"}
    assert update_context.status == 404

    delete_context = Context()
    deleted = await plugins["delete"].execute({"id": 999}, delete_context)
    assert deleted == {"success": False, "error": "Stream output not found"}
    assert delete_context.status == 404


async def test_stream_tool_and_runtime_status(db):
    from tools.stream_tool.stream_tool import StreamTool
    from domains.stream_outputs.plugins.start_stream_output_plugin import StartStreamOutputPlugin
    from domains.stream_outputs.plugins.stop_stream_output_plugin import StopStreamOutputPlugin
    from domains.stream_outputs.plugins.start_active_stream_outputs_plugin import StartActiveStreamOutputsPlugin
    from domains.stream_outputs.plugins.stop_active_stream_outputs_plugin import StopActiveStreamOutputsPlugin
    from domains.stream_outputs.plugins.stream_runtime_status_plugin import StreamRuntimeStatusPlugin

    http = FakeHttp()
    logger = FakeLogger()
    tool = StreamTool()
    tool.db = db
    await tool.setup()

    # Insert test outputs
    await db.execute(
        """
        INSERT INTO stream_outputs (id, name, platform, channel_id, enabled, rtmp_url, stream_key_secret, status)
        VALUES (1, 'Twitch', 'twitch', 'user1', 1, 'rtmp://live.twitch.tv/app', 'live_key_123', 'stopped'),
               (2, 'YouTube', 'youtube', 'UC123', 0, 'rtmp://a.rtmp.youtube.com/live2', 'live_key_456', 'stopped')
        """
    )

    start_plugin = StartStreamOutputPlugin(http, tool, logger)
    stop_plugin = StopStreamOutputPlugin(http, tool, logger)
    start_active_plugin = StartActiveStreamOutputsPlugin(http, tool, logger)
    stop_active_plugin = StopActiveStreamOutputsPlugin(http, tool, logger)
    runtime_plugin = StreamRuntimeStatusPlugin(http, tool, logger)

    await start_plugin.on_boot()
    await stop_plugin.on_boot()
    await start_active_plugin.on_boot()
    await stop_active_plugin.on_boot()
    await runtime_plugin.on_boot()

    # Test runtime status
    status = await runtime_plugin.execute({})
    assert status["success"] is True
    assert status["data"]["obs_connected"] is False
    assert status["data"]["live_outputs_count"] == 0
    assert status["data"]["enabled_outputs_count"] == 1

    # Test stop output
    stopped = await stop_plugin.execute({"id": 1})
    assert stopped["success"] is True
    assert stopped["data"]["status"] == "stopped"

    # Test stop active
    stopped_all = await stop_active_plugin.execute({})
    assert stopped_all["success"] is True

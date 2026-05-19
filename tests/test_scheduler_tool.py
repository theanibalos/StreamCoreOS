import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from tools.scheduler.scheduler_tool import SchedulerTool

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def tool():
    t = SchedulerTool()
    t.setup()
    await t.on_boot_complete(None)
    yield t
    t.shutdown()


async def test_add_job_returns_string_id(tool):
    job_id = tool.add_job("* * * * *", lambda: None)
    assert isinstance(job_id, str) and job_id


async def test_add_job_same_id_no_duplicate(tool):
    tool.add_job("* * * * *", lambda: None, job_id="stable")
    tool.add_job("* * * * *", lambda: None, job_id="stable")
    ids = [j["id"] for j in tool.list_jobs() if j["id"] == "stable"]
    assert len(ids) == 1


async def test_list_jobs_contains_registered_job(tool):
    job_id = tool.add_job("* * * * *", lambda: None, job_id="listed")
    jobs = tool.list_jobs()
    assert any(j["id"] == job_id for j in jobs)


async def test_remove_job_returns_true_and_removes(tool):
    job_id = tool.add_job("* * * * *", lambda: None)
    assert tool.remove_job(job_id) is True
    assert not any(j["id"] == job_id for j in tool.list_jobs())


async def test_remove_job_nonexistent_returns_false(tool):
    assert tool.remove_job("does-not-exist") is False


async def test_add_one_shot_returns_job_id(tool):
    run_at = datetime.now(timezone.utc) + timedelta(hours=1)
    job_id = tool.add_one_shot(run_at, lambda: None)
    assert isinstance(job_id, str) and job_id
    jobs = tool.list_jobs()
    assert any(j["id"] == job_id for j in jobs)


async def test_one_shot_job_executes_callback(tool):
    executed = []

    def callback():
        executed.append(True)

    soon = datetime.now(timezone.utc) + timedelta(milliseconds=200)
    tool.add_one_shot(soon, callback)

    await asyncio.sleep(1.5)

    assert len(executed) == 1


async def test_async_one_shot_job_executes_callback(tool):
    executed = []

    async def async_callback():
        executed.append(True)

    soon = datetime.now(timezone.utc) + timedelta(milliseconds=200)
    tool.add_one_shot(soon, async_callback)

    await asyncio.sleep(1.5)

    assert len(executed) == 1


async def test_list_jobs_entry_structure(tool):
    tool.add_job("* * * * *", lambda: None, job_id="struct_test")
    jobs = tool.list_jobs()
    entry = next(j for j in jobs if j["id"] == "struct_test")
    assert "id" in entry
    assert "next_run" in entry
    assert "trigger" in entry
    assert isinstance(entry["id"], str)
    assert isinstance(entry["trigger"], str)
    assert entry["next_run"] is None or isinstance(entry["next_run"], str)

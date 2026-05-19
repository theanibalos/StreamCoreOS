import pytest

from extras.available_tools.postgresql.postgresql_tool import (
    PostgresqlTool,
    _parse_affected_rows,
    DatabaseConnectionError,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def pg_env(monkeypatch):
    monkeypatch.setenv("PG_HOST", "localhost")
    monkeypatch.setenv("PG_PORT", "5432")
    monkeypatch.setenv("PG_USER", "postgres")
    monkeypatch.setenv("PG_PASSWORD", "postgres")
    monkeypatch.setenv("PG_DATABASE", "microcoreos")


# ── Unit tests (no Docker) ──────────────────────────────────────────────────

def test_parse_update():
    assert _parse_affected_rows("UPDATE 3") == 3

def test_parse_delete():
    assert _parse_affected_rows("DELETE 1") == 1

def test_parse_insert_returning():
    assert _parse_affected_rows("INSERT 0 1") == 1

def test_parse_select():
    assert _parse_affected_rows("SELECT 0") == 0

def test_parse_empty():
    assert _parse_affected_rows("") == 0


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
async def tool():
    t = PostgresqlTool()
    await t.setup()
    yield t
    await t.execute("DROP TABLE IF EXISTS _test")
    await t.shutdown()


@pytest.fixture
async def tool_with_table(tool):
    await tool.execute("CREATE TABLE _test (id SERIAL PRIMARY KEY, name TEXT)")
    return tool


# ── Integration tests (Docker) ──────────────────────────────────────────────

async def test_create_table(tool):
    await tool.execute("CREATE TABLE _test (id SERIAL PRIMARY KEY, name TEXT)")

async def test_insert_returning(tool_with_table):
    row_id = await tool_with_table.execute("INSERT INTO _test (name) VALUES ($1) RETURNING id", ["Ana"])
    assert isinstance(row_id, int) and row_id >= 1

async def test_query(tool_with_table):
    await tool_with_table.execute("INSERT INTO _test (name) VALUES ($1) RETURNING id", ["Ana"])
    rows = await tool_with_table.query("SELECT * FROM _test")
    assert len(rows) == 1

async def test_query_one(tool_with_table):
    row_id = await tool_with_table.execute("INSERT INTO _test (name) VALUES ($1) RETURNING id", ["Ana"])
    row = await tool_with_table.query_one("SELECT * FROM _test WHERE id = $1", [row_id])
    assert row is not None
    assert row["name"] == "Ana"

async def test_query_one_missing(tool_with_table):
    row = await tool_with_table.query_one("SELECT * FROM _test WHERE id = $1", [99999])
    assert row is None

async def test_update_affected_rows(tool_with_table):
    row_id = await tool_with_table.execute("INSERT INTO _test (name) VALUES ($1) RETURNING id", ["Ana"])
    affected = await tool_with_table.execute("UPDATE _test SET name = $1 WHERE id = $2", ["Bob", row_id])
    assert affected == 1

async def test_execute_many(tool_with_table):
    await tool_with_table.execute_many("INSERT INTO _test (name) VALUES ($1)", [["X"], ["Y"]])
    rows = await tool_with_table.query("SELECT * FROM _test")
    assert len(rows) == 2

async def test_execute_many_empty_list_is_noop(tool_with_table):
    await tool_with_table.execute_many("INSERT INTO _test (name) VALUES ($1)", [])
    rows = await tool_with_table.query("SELECT * FROM _test")
    assert rows == []

async def test_transaction_commit(tool_with_table):
    async with tool_with_table.transaction() as tx:
        await tx.execute("INSERT INTO _test (name) VALUES ($1)", ["TxA"])
        await tx.execute("INSERT INTO _test (name) VALUES ($1)", ["TxB"])
    rows = await tool_with_table.query("SELECT * FROM _test")
    assert len(rows) == 2

async def test_transaction_rollback(tool_with_table):
    try:
        async with tool_with_table.transaction() as tx:
            await tx.execute("INSERT INTO _test (name) VALUES ($1)", ["WillRollback"])
            raise ValueError("forced rollback")
    except ValueError:
        pass
    rows = await tool_with_table.query("SELECT * FROM _test")
    assert len(rows) == 0

async def test_health_check_active(tool):
    assert await tool.health_check() is True

async def test_health_check_no_pool():
    t = PostgresqlTool()
    assert await t.health_check() is False

async def test_invalid_credentials(monkeypatch):
    monkeypatch.setenv("PG_PASSWORD", "wrong_password")
    t = PostgresqlTool()
    with pytest.raises(DatabaseConnectionError):
        await t.setup()

async def test_transaction_raises_when_pool_not_initialized():
    t = PostgresqlTool()  # sin llamar setup() — pool es None
    with pytest.raises(DatabaseConnectionError):
        async with t.transaction():
            pass

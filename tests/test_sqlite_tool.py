import pytest

from tools.sqlite.sqlite_tool import SqliteTool, _normalize_sql

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db(monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", ":memory:")
    tool = SqliteTool()
    await tool.setup()
    yield tool
    await tool.shutdown()


@pytest.fixture
async def db_with_table(db):
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
    return db


# ── _normalize_sql (sync, sin DB) ─────────────────────────────────────────────

def test_normalize_single_placeholder():
    sql, params = _normalize_sql("SELECT * FROM t WHERE id = $1", [42])
    assert "?" in sql
    assert "$1" not in sql
    assert params == [42]


def test_normalize_multiple_placeholders():
    sql, params = _normalize_sql("INSERT INTO t VALUES ($1, $2)", ["a", "b"])
    assert sql.count("?") == 2
    assert params == ["a", "b"]


def test_normalize_no_placeholders():
    original = "SELECT * FROM t"
    sql, params = _normalize_sql(original, [])
    assert sql == original
    assert params == []


# ── Operaciones reales ────────────────────────────────────────────────────────

async def test_execute_create_table(db):
    await db.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")


async def test_execute_insert_returns_id(db_with_table):
    row_id = await db_with_table.execute("INSERT INTO t (name) VALUES ($1)", ["Ana"])
    assert row_id == 1


async def test_query_returns_rows(db_with_table):
    await db_with_table.execute("INSERT INTO t (name) VALUES ($1)", ["Ana"])
    rows = await db_with_table.query("SELECT * FROM t")
    assert rows == [{"id": 1, "name": "Ana"}]


async def test_query_one_found(db_with_table):
    await db_with_table.execute("INSERT INTO t (name) VALUES ($1)", ["Ana"])
    row = await db_with_table.query_one("SELECT * FROM t WHERE id = $1", [1])
    assert row == {"id": 1, "name": "Ana"}


async def test_query_one_not_found(db_with_table):
    row = await db_with_table.query_one("SELECT * FROM t WHERE id = $1", [999])
    assert row is None


async def test_execute_update_returns_affected_rows(db_with_table):
    await db_with_table.execute("INSERT INTO t (name) VALUES ($1)", ["Ana"])
    affected = await db_with_table.execute(
        "UPDATE t SET name = $1 WHERE id = $2", ["Bob", 1]
    )
    assert affected == 1


async def test_execute_many_inserts_all(db_with_table):
    await db_with_table.execute_many(
        "INSERT INTO t (name) VALUES ($1)", [["X"], ["Y"], ["Z"]]
    )
    rows = await db_with_table.query("SELECT * FROM t")
    assert len(rows) == 3


# ── Transactions ──────────────────────────────────────────────────────────────

async def test_transaction_commits(db_with_table):
    async with db_with_table.transaction() as tx:
        await tx.execute("INSERT INTO t (name) VALUES ($1)", ["Alice"])
        await tx.execute("INSERT INTO t (name) VALUES ($1)", ["Bob"])
    rows = await db_with_table.query("SELECT * FROM t")
    assert len(rows) == 2


async def test_transaction_rollback_on_exception(db_with_table):
    with pytest.raises(RuntimeError):
        async with db_with_table.transaction() as tx:
            await tx.execute("INSERT INTO t (name) VALUES ($1)", ["Alice"])
            raise RuntimeError("oops")
    rows = await db_with_table.query("SELECT * FROM t")
    assert rows == []


async def test_nested_transaction_inner_rollback_no_affect_outer(db_with_table):
    async with db_with_table.transaction() as outer:
        await outer.execute("INSERT INTO t (name) VALUES ($1)", ["outer"])
        try:
            async with db_with_table.transaction() as inner:
                await inner.execute("INSERT INTO t (name) VALUES ($1)", ["inner"])
                raise RuntimeError("inner fail")
        except RuntimeError:
            pass
    rows = await db_with_table.query("SELECT * FROM t")
    names = [r["name"] for r in rows]
    assert "outer" in names
    assert "inner" not in names


async def test_execute_insert_with_returning(db_with_table):
    row_id = await db_with_table.execute(
        "INSERT INTO t (name) VALUES ($1) RETURNING id", ["Ana"]
    )
    assert row_id == 1


async def test_transaction_raises_when_no_connection(db):
    db._db = None
    with pytest.raises(Exception):
        async with db.transaction():
            pass


# ── Health check ──────────────────────────────────────────────────────────────

async def test_health_check_active(db):
    assert await db.health_check() is True


async def test_health_check_no_connection(db):
    db._db = None
    assert await db.health_check() is False


# ── execute_many vacío ────────────────────────────────────────────────────────

async def test_execute_many_empty_list_is_noop(db_with_table):
    await db_with_table.execute_many("INSERT INTO t (name) VALUES ($1)", [])
    rows = await db_with_table.query("SELECT * FROM t")
    assert rows == []


# ── query sin resultados ───────────────────────────────────────────────────────

async def test_query_returns_empty_list_when_no_rows(db_with_table):
    rows = await db_with_table.query("SELECT * FROM t")
    assert rows == []


# ── execute DELETE ─────────────────────────────────────────────────────────────

async def test_execute_delete_returns_affected_rows(db_with_table):
    await db_with_table.execute("INSERT INTO t (name) VALUES ($1)", ["Ana"])
    affected = await db_with_table.execute("DELETE FROM t WHERE id = $1", [1])
    assert affected == 1
    rows = await db_with_table.query("SELECT * FROM t")
    assert rows == []


async def test_execute_delete_no_match_returns_zero(db_with_table):
    affected = await db_with_table.execute("DELETE FROM t WHERE id = $1", [999])
    assert affected == 0

import pytest
from tools.sqlite.sqlite_tool import SqliteTool

pytestmark = pytest.mark.anyio

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.fixture
async def db(monkeypatch, tmp_path):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))
    monkeypatch.chdir(tmp_path)
    tool = SqliteTool()
    await tool.setup()
    yield tool
    await tool.shutdown()

async def test_migration_topological_sort_intent(db, tmp_path, monkeypatch):
    """
    The intent is that migrations run in the order of their declared
    dependencies, not just by filename.
    """
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    
    users_dir = domains_dir / "users" / "migrations"
    users_dir.mkdir(parents=True)
    (users_dir / "001_create_users.sql").write_text("CREATE TABLE users (id int);", encoding="utf-8")
    
    profiles_dir = domains_dir / "profiles" / "migrations"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "000_create_profiles.sql").write_text(
        "-- depends: users/001_create_users.sql\n"
        "CREATE TABLE profiles (user_id int, FOREIGN KEY(user_id) REFERENCES users(id));",
        encoding="utf-8",
    )
    
    monkeypatch.chdir(tmp_path)
    await db._run_migrations()
    
    tables = await db.query("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = [t["name"] for t in tables]
    
    assert "users" in table_names
    assert "profiles" in table_names
    
    history = await db.query("SELECT filename FROM _migrations_history ORDER BY id ASC")
    order = [h["filename"] for h in history]
    
    assert order == ["001_create_users.sql", "000_create_profiles.sql"]

async def test_db_auto_migrate_false_skips_migrations(db, tmp_path, monkeypatch):
    domains_dir = tmp_path / "domains"
    users_dir = domains_dir / "users" / "migrations"
    users_dir.mkdir(parents=True)
    (users_dir / "001_create_users.sql").write_text("CREATE TABLE users (id int);", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_AUTO_MIGRATE", "false")

    await db._run_migrations()

    tables = await db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    assert tables == []

async def test_migration_transaction_safety_intent(db, tmp_path, monkeypatch):
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    
    blog_dir = domains_dir / "blog" / "migrations"
    blog_dir.mkdir(parents=True)
    (blog_dir / "001_fail.sql").write_text(
        "CREATE TABLE blog_posts (id int);\n"
        "INVALID SQL STATEMENT;",
        encoding="utf-8",
    )
    
    monkeypatch.chdir(tmp_path)
    
    with pytest.raises(Exception):
        await db._run_migrations()
        
    tables = await db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='blog_posts'")
    assert len(tables) == 0
    
    history = await db.query("SELECT * FROM _migrations_history WHERE domain='blog'")
    assert len(history) == 0

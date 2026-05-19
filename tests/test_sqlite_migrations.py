import os
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
    tool = SqliteTool()
    await tool.setup()
    yield tool
    await tool.shutdown()

async def test_migration_topological_sort_intent(db, tmp_path, monkeypatch):
    """
    La intención es que las migraciones se ejecuten en el orden de sus dependencias
    declaradas, no solo por nombre de archivo.
    
    Escenario:
    002_profiles depende de 001_users.
    Si se ejecutan alfabéticamente funcionaría, pero si forzamos una dependencia 
    cruzada o inversa, el orden topológico debe prevalecer.
    """
    # Creamos estructura de dominios en un directorio temporal
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    
    # Dominio A: Usuarios (sin dependencias)
    users_dir = domains_dir / "users" / "migrations"
    users_dir.mkdir(parents=True)
    (users_dir / "001_create_users.sql").write_text("CREATE TABLE users (id int);")
    
    # Dominio B: Perfiles (depende de usuarios)
    profiles_dir = domains_dir / "profiles" / "migrations"
    profiles_dir.mkdir(parents=True)
    # Nota el prefijo 000 para que alfabéticamente vaya primero, pero la dependencia lo mueva al final
    (profiles_dir / "000_create_profiles.sql").write_text(
        "-- depends: users/001_create_users.sql\n"
        "CREATE TABLE profiles (user_id int, FOREIGN KEY(user_id) REFERENCES users(id));"
    )
    
    # Cambiamos al directorio temporal para que el tool encuentre 'domains/'
    monkeypatch.chdir(tmp_path)
    
    # Ejecutamos boot (que corre las migraciones)
    await db.on_boot_complete(None)
    
    # Verificamos que AMBAS tablas existan (si el orden fallara, la FK de profiles daría error si users no existe)
    tables = await db.query("SELECT name FROM sqlite_master WHERE type='table'")
    table_names = [t["name"] for t in tables]
    
    assert "users" in table_names
    assert "profiles" in table_names
    
    # Verificamos el historial de migraciones para ver el orden real de aplicación
    history = await db.query("SELECT filename FROM _migrations_history ORDER BY id ASC")
    order = [h["filename"] for h in history]
    
    # La intención es que 001_create_users se haya ejecutado ANTES que 000_create_profiles
    assert order == ["001_create_users.sql", "000_create_profiles.sql"]

async def test_migration_transaction_safety_intent(db, tmp_path, monkeypatch):
    """
    La intención es que cada archivo de migración sea atómico. Si una sentencia
    falla, NADA de ese archivo debe quedar en la base de datos ni en el historial.
    """
    domains_dir = tmp_path / "domains"
    domains_dir.mkdir()
    
    blog_dir = domains_dir / "blog" / "migrations"
    blog_dir.mkdir(parents=True)
    # Esta migración crea una tabla y luego falla
    (blog_dir / "001_fail.sql").write_text(
        "CREATE TABLE blog_posts (id int);\n"
        "INVALID SQL STATEMENT;"
    )
    
    monkeypatch.chdir(tmp_path)
    
    with pytest.raises(Exception):
        await db.on_boot_complete(None)
        
    # La tabla blog_posts NO debe existir (Rollback)
    tables = await db.query("SELECT name FROM sqlite_master WHERE type='table' AND name='blog_posts'")
    assert len(tables) == 0
    
    # No debe haber rastro en el historial
    history = await db.query("SELECT * FROM _migrations_history WHERE domain='blog'")
    assert len(history) == 0

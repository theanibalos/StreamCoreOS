"""
SQLite Tool — Drop-in Replacement for PostgreSQL in MicroCoreOS
================================================================

100% COMPATIBLE with the PostgreSQL gold-standard contract.
Plugins write PostgreSQL-style SQL ($1, $2...) and this tool
transparently converts placeholders to SQLite's native '?'.

PUBLIC CONTRACT (IDENTICAL to PostgreSQL — same SQL, same swap):
─────────────────────────────────────────────────────────────────
    rows  = await db.query("SELECT * FROM users WHERE age > $1", [18])
    row   = await db.query_one("SELECT * FROM users WHERE id = $1", [5])
    newid = await db.execute("INSERT INTO users (name) VALUES ($1)", ["Ana"])
    count = await db.execute("UPDATE users SET active = $1", [True])
    await db.execute_many("INSERT INTO logs (msg) VALUES ($1)", [["a"], ["b"]])

    async with db.transaction() as tx:
        uid = await tx.execute("INSERT INTO users (name) VALUES ($1) RETURNING id", ["Ana"])
        await tx.execute("INSERT INTO profiles (user_id) VALUES ($1)", [uid])
        # Auto-COMMIT on exit. Auto-ROLLBACK on exception.

    ok = await db.health_check()

PLACEHOLDERS: Plugins ALWAYS use $1, $2, $3... (PostgreSQL-style).
              This tool converts them internally to '?' for SQLite.
              This enables direct SQLite <-> PostgreSQL swap without changing a line.

⚠ MIGRATION FILES ARE NOT CROSS-COMPATIBLE:
  The .sql files in domains/*/migrations/ may contain engine-specific DDL
  (e.g. PostgreSQL's SERIAL, TIMESTAMPTZ, JSONB vs SQLite's INTEGER PRIMARY KEY
  AUTOINCREMENT, TEXT). When swapping engines, migration files will likely need
  to be rewritten. This is by design — migration SQL is cheap to regenerate.
"""

import os
import re
import uuid
import asyncio
import aiosqlite
from contextvars import ContextVar
from core.base_tool import BaseTool, ToolUnavailableError


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONTEXT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Tracks if the current task holds the SQLite write lock to allow reentrancy
_write_lock_held_var: ContextVar[bool] = ContextVar("sqlite_write_lock_held", default=False)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXCEPTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DatabaseError(Exception):
    """Generic database error. Wraps aiosqlite exceptions."""
    pass


class DatabaseConnectionError(DatabaseError, ToolUnavailableError):
    """Connection error to the SQLite file.

    Inherits ToolUnavailableError so ToolProxy marks the tool DEAD immediately
    (infrastructure failure), unlike plain DatabaseError (likely business error).
    """
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PLACEHOLDER NORMALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PG_PLACEHOLDER = re.compile(r'\$(\d+)')

def _normalize_sql(sql: str, params: list | None = None) -> tuple[str, list]:
    """
    Converts PostgreSQL placeholders ($1, $2...) to SQLite (?).
    Expands params to match the positional placeholders.
    """
    params = params or []
    matches = _PG_PLACEHOLDER.findall(sql)
    if not matches:
        return sql, params
        
    new_params = []
    for m in matches:
        idx = int(m) - 1
        if 0 <= idx < len(params):
            new_params.append(params[idx])
        else:
            new_params.append(None)
            
    sql = _PG_PLACEHOLDER.sub('?', sql)
    return sql, new_params

def _normalize_sql_many(sql: str, params_list: list[list]) -> tuple[str, list[list]]:
    matches = _PG_PLACEHOLDER.findall(sql)
    if not matches:
        return sql, params_list
        
    sql = _PG_PLACEHOLDER.sub('?', sql)
    new_params_list = []
    for params in params_list:
        new_params = []
        for m in matches:
            idx = int(m) - 1
            if 0 <= idx < len(params):
                new_params.append(params[idx])
            else:
                new_params.append(None)
        new_params_list.append(new_params)
        
    return sql, new_params_list


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRANSACTION CONTEXT MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Transaction:
    """
    Explicit transaction over the SQLite connection.

    Usage:
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO ...", [...])
            await tx.execute("UPDATE ...", [...])
            rows = await tx.query("SELECT ...", [...])
        # Auto-COMMIT on block exit.
        # Auto-ROLLBACK on any exception.

    The context manager handles:
    1. Opening a real SQLite transaction (SAVEPOINT).
    2. RELEASE (commit) if everything succeeds.
    3. ROLLBACK if an exception occurs.
    """

    def __init__(self, db: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._db: aiosqlite.Connection = db
        self._lock: asyncio.Lock = lock
        self._savepoint_name: str | None = None
        self._acquired_lock: bool = False

    async def __aenter__(self) -> "Transaction":
        try:
            # Check for reentrancy (nested transactions)
            if not _write_lock_held_var.get():
                await self._lock.acquire()
                self._acquired_lock = True
                _write_lock_held_var.set(True)

            # Use SAVEPOINTs to support nested transactions
            self._savepoint_name = f"sp_{uuid.uuid4().hex}"
            await self._db.execute(f"SAVEPOINT {self._savepoint_name}")
        except Exception as e:
            if self._acquired_lock:
                _write_lock_held_var.set(False)
                self._lock.release()
            raise DatabaseConnectionError(f"Failed to start transaction: {e}") from e
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type is None:
                # No errors → RELEASE SAVEPOINT (equivalent to COMMIT)
                await self._db.execute(f"RELEASE SAVEPOINT {self._savepoint_name}")
            else:
                # Errors → ROLLBACK TO SAVEPOINT
                await self._db.execute(f"ROLLBACK TO SAVEPOINT {self._savepoint_name}")
        except Exception:
            pass
        finally:
            # Only the transaction that acquired the lock should release it
            if self._acquired_lock:
                _write_lock_held_var.set(False)
                if self._lock.locked():
                    self._lock.release()
        return False

    # ─── Transaction API ──────────────────────────────────

    async def query(self, sql: str, params: list | None = None) -> list[dict]:
        """SELECT within the transaction. Returns list[dict]."""
        sql, params = _normalize_sql(sql, params)
        try:
            cursor = await self._db.execute(sql, params)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = await cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            raise DatabaseError(f"Transaction query failed: {e}") from e

    async def query_one(self, sql: str, params: list | None = None) -> dict | None:
        """SELECT a single record within the transaction. Returns dict or None."""
        sql, params = _normalize_sql(sql, params)
        try:
            cursor = await self._db.execute(sql, params)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            row = await cursor.fetchone()
            return dict(zip(columns, row)) if row is not None else None
        except Exception as e:
            raise DatabaseError(f"Transaction query_one failed: {e}") from e

    async def execute(self, sql: str, params: list | None = None) -> int | None:
        """
        INSERT/UPDATE/DELETE within the transaction.

        - If the SQL contains RETURNING, returns the first column value
          of the first row (typically the generated ID).
        - If INSERT without RETURNING, returns lastrowid.
        - Otherwise, returns the number of affected rows.
        """
        sql, params = _normalize_sql(sql, params)
        try:
            if "RETURNING" in sql.upper():
                cursor = await self._db.execute(sql, params)
                row = await cursor.fetchone()
                if row is not None:
                    return row[0]
                return None
            else:
                cursor = await self._db.execute(sql, params)
                if sql.strip().upper().startswith("INSERT"):
                    return cursor.lastrowid
                return cursor.rowcount
        except Exception as e:
            raise DatabaseError(f"Transaction execute failed: {e}") from e


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SQLITE TOOL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SqliteTool(BaseTool):
    """
    SQLite persistence tool for MicroCoreOS.

    DROP-IN REPLACEMENT for PostgreSQL. Accepts PostgreSQL-style
    placeholders ($1, $2...), converting them to '?' internally.
    Swap between SQLite and PostgreSQL with zero plugin changes.

    Uses aiosqlite for non-blocking access to a local SQLite database file.
    Ideal for development, testing, and lightweight deployments.
    """

    # ─── IDENTITY ─────────────────────────────────────────

    @property
    def name(self) -> str:
        return "db"

    # ─── CONSTRUCTOR ──────────────────────────────────────
    #
    # Configuration reading only. Zero logic, zero I/O.
    # The connection is created in setup(), NOT here.
    #

    def __init__(self) -> None:
        self._db_path: str = os.getenv("SQLITE_DB_PATH", "database.db")
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    # ─── LIFECYCLE: setup() ───────────────────────────────
    #
    # Infrastructure phase. Runs BEFORE plugins.
    # Responsibilities:
    #   1. Open the connection to the SQLite database.
    #   2. Enable WAL mode and foreign keys.
    #   3. Create the internal migration history table.
    #

    async def setup(self) -> None:
        print(f"[System] SqliteTool: Opening {self._db_path}...")

        try:
            self._db = await aiosqlite.connect(self._db_path)
            # Enable Write-Ahead Logging for better concurrency
            await self._db.execute("PRAGMA journal_mode=WAL")
            # Enable Foreign Keys (disabled by default in SQLite)
            await self._db.execute("PRAGMA foreign_keys=ON")
            await self._db.commit()
        except Exception as e:
            raise DatabaseConnectionError(
                f"Cannot open SQLite database at {self._db_path}: {e}"
            ) from e

        # Create internal migration history table
        await self.execute("""
            CREATE TABLE IF NOT EXISTS _migrations_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                domain      TEXT NOT NULL,
                filename    TEXT NOT NULL,
                applied_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(domain, filename)
            )
        """)

        print("[System] SqliteTool: Ready (WAL mode, FK enabled).")

    # ─── LIFECYCLE: on_boot_complete() ────────────────────
    #
    # Runs AFTER all tools and plugins are loaded.
    # Responsibility: execute pending SQL migrations.
    #
    # Migrations are located in: domains/*/migrations/*.sql
    # Applied in alphabetical order, each within its own transaction.
    # If a migration fails, that migration is rolled back
    # and execution stops (raise) to prevent an inconsistent state.
    #

    async def on_boot_complete(self, container) -> None:
        # Issue 20: in production, replicas must NOT race to migrate at boot.
        # Migrations run as a pipeline step instead:
        #   DB_AUTO_MIGRATE=true uv run main.py --boot-tool db
        if os.getenv("DB_AUTO_MIGRATE", "true").strip().lower() != "true":
            print("[System] SqliteTool: DB_AUTO_MIGRATE=false — skipping migrations (pipeline runs `DB_AUTO_MIGRATE=true uv run main.py --boot-tool db`).")
            return
        print("[System] SqliteTool: Checking for pending migrations...")
        domains_dir = os.path.abspath("domains")
        if not os.path.exists(domains_dir):
            return

        # ── 1. Discover ALL migration files across all domains ──────────
        migrations = {}  # key: "domain/filename" → value: {"path": ..., "depends": [...]}
        for domain in sorted(os.listdir(domains_dir)):
            migrations_dir = os.path.join(domains_dir, domain, "migrations")
            if not os.path.isdir(migrations_dir):
                continue

            for filename in sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql")):
                key = f"{domain}/{filename}"
                filepath = os.path.join(migrations_dir, filename)

                # Parse "-- depends: domain/filename" from first lines
                depends = []
                with open(filepath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.lower().startswith("-- depends:"):
                            dep = line.split(":", 1)[1].strip()
                            # Allow "-- depends: users/001_create_users_table" (with or without .sql)
                            if not dep.endswith(".sql"):
                                dep += ".sql"
                            depends.append(dep)
                        elif line.startswith("--"):
                            continue  # skip other comments
                        else:
                            break  # stop parsing after first non-comment line

                migrations[key] = {"path": filepath, "depends": depends, "domain": domain, "filename": filename}

        # ── 2. Topological sort using graphlib ──────────────────────────
        from graphlib import TopologicalSorter

        graph = {}
        for key, info in migrations.items():
            graph[key] = set(info["depends"])

        try:
            sorter = TopologicalSorter(graph)
            ordered_keys = list(sorter.static_order())
        except Exception as e:
            print(f"  [Migration] ⚠️  Circular dependency detected: {e}")
            # Fallback to alphabetical
            ordered_keys = sorted(migrations.keys())

        # ── 3. Apply in topological order ───────────────────────────────
        for key in ordered_keys:
            if key not in migrations:
                continue  # dependency references a migration that doesn't exist (yet)

            info = migrations[key]
            domain = info["domain"]
            filename = info["filename"]

            # Check if already applied
            already_applied = await self.query_one(
                "SELECT 1 FROM _migrations_history WHERE domain = $1 AND filename = $2",
                [domain, filename],
            )
            if already_applied:
                continue

            print(f"  [Migration] Applying {key}...")

            with open(info["path"], "r", encoding="utf-8") as f:
                lines = f.readlines()
                sql_script = "\n".join(line for line in lines if not line.strip().startswith("--"))

            # Each migration in its own transaction
            try:
                async with self.transaction():
                    # Manually split and execute to ensure atomicity via our Transaction CM
                    # This handles triggers if we are careful, but for now we split by ';'
                    # which is what the user originally had but now inside our safe TX.
                    statements = [s.strip() for s in sql_script.split(";") if s.strip()]
                    for statement in statements:
                        await self._db.execute(statement)
                    
                    # Register successful migration
                    await self._db.execute(
                        "INSERT INTO _migrations_history (domain, filename) VALUES (?, ?)",
                        [domain, filename],
                    )
                    # transaction __aexit__ will COMMIT
            except Exception as e:
                # transaction __aexit__ will ROLLBACK
                raise DatabaseError(f"Migration failed for {key}: {e}") from e

            print(f"  [Migration] ✅ Applied {key}")

    # ─── LIFECYCLE: shutdown() ────────────────────────────
    #
    # Closes the connection gracefully.
    #

    async def shutdown(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            print("[SqliteTool] Connection closed.")

    # ─── PUBLIC API: query() ──────────────────────────────
    #
    # Executes a SELECT and returns ALL records.
    #
    # Parameters:
    #   sql:    str           — SQL query with $1, $2... placeholders
    #   params: list | None   — Values for the placeholders
    #
    # Returns: list[dict]
    #   - Empty list if no results.
    #   - Each dict has column names as keys.
    #
    # Example:
    #   rows = await db.query("SELECT id, name FROM users WHERE age > $1", [18])
    #   # [{"id": 1, "name": "Ana"}, {"id": 2, "name": "Luis"}]
    #

    async def query(self, sql: str, params: list | None = None) -> list[dict]:
        sql, params = _normalize_sql(sql, params)
        for attempt in range(3):
            try:
                cursor = await self._db.execute(sql, params)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = await cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) or "database is busy" in str(e):
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                raise DatabaseError(f"Query failed: {e}") from e
            except Exception as e:
                raise DatabaseError(f"Query failed: {e}") from e

    # ─── PUBLIC API: query_one() ──────────────────────────
    #
    # Executes a SELECT and returns the FIRST record or None.
    #
    # Parameters:
    #   sql:    str           — SQL query with $1, $2... placeholders
    #   params: list | None   — Values for the placeholders
    #
    # Returns: dict | None
    #   - None if no results.
    #   - dict with column names as keys.
    #
    # Example:
    #   user = await db.query_one("SELECT * FROM users WHERE id = $1", [5])
    #   # {"id": 5, "name": "Ana", "email": "ana@mail.com"} or None
    #

    async def query_one(self, sql: str, params: list | None = None) -> dict | None:
        sql, params = _normalize_sql(sql, params)
        for attempt in range(3):
            try:
                cursor = await self._db.execute(sql, params)
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                row = await cursor.fetchone()
                return dict(zip(columns, row)) if row is not None else None
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) or "database is busy" in str(e):
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                raise DatabaseError(f"Query failed: {e}") from e
            except Exception as e:
                raise DatabaseError(f"Query failed: {e}") from e

    # ─── PUBLIC API: execute() ────────────────────────────
    #
    # Executes INSERT, UPDATE or DELETE.
    #
    # Parameters:
    #   sql:    str           — SQL with $1, $2... placeholders
    #   params: list | None   — Values for the placeholders
    #
    # Returns: int | None
    #   - With RETURNING (SQLite 3.35+): the first column value
    #     of the first row (typically the generated ID).
    #   - INSERT without RETURNING: returns lastrowid (the generated ID).
    #   - UPDATE/DELETE without RETURNING: the number of affected rows (int).
    #
    # Example INSERT (lastrowid):
    #   new_id = await db.execute(
    #       "INSERT INTO users (name) VALUES ($1)", ["Ana"]
    #   )
    #   # 42
    #
    # Example with RETURNING (SQLite 3.35+):
    #   new_id = await db.execute(
    #       "INSERT INTO users (name) VALUES ($1) RETURNING id", ["Ana"]
    #   )
    #   # 42
    #
    # Example without RETURNING:
    #   affected = await db.execute(
    #       "UPDATE users SET active = $1 WHERE age < $2", [False, 18]
    #   )
    #   # 3
    #

    async def execute(self, sql: str, params: list | None = None) -> int | None:
        sql, params = _normalize_sql(sql, params)
        
        # Reentrancy check: if the lock is already held by this task, don't acquire it again
        if _write_lock_held_var.get():
            return await self._do_execute(sql, params)

        async with self._write_lock:
            token = _write_lock_held_var.set(True)
            try:
                return await self._do_execute(sql, params)
            finally:
                _write_lock_held_var.reset(token)

    async def _do_execute(self, sql: str, params: list | None) -> int | None:
        """Internal execution logic with retry capability."""
        for attempt in range(3):
            try:
                if re.search(r"\bRETURNING\b", sql.upper()):
                    cursor = await self._db.execute(sql, params)
                    row = await cursor.fetchone()
                    await self._db.commit()
                    if row is not None:
                        return row[0]
                    return None
                else:
                    cursor = await self._db.execute(sql, params)
                    await self._db.commit()
                    if sql.strip().upper().startswith("INSERT"):
                        return cursor.lastrowid
                    return cursor.rowcount
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) or "database is busy" in str(e):
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                raise DatabaseError(f"Execute failed: {e}") from e
            except Exception as e:
                raise DatabaseError(f"Execute failed: {e}") from e

    # ─── PUBLIC API: execute_many() ───────────────────────
    #
    # Executes the same SQL statement with multiple parameter sets.
    #
    # Parameters:
    #   sql:         str         — SQL with $1, $2... placeholders
    #   params_list: list[list]  — List of parameter lists.
    #
    # Returns: None
    #
    # Example:
    #   await db.execute_many(
    #       "INSERT INTO logs (level, msg) VALUES ($1, $2)",
    #       [["INFO", "Started"], ["ERROR", "Crashed"], ["INFO", "Recovered"]]
    #   )
    #

    async def execute_many(self, sql: str, params_list: list[list]) -> None:
        sql, params_list = _normalize_sql_many(sql, params_list)
        
        # Reentrancy check
        if _write_lock_held_var.get():
            await self._do_execute_many(sql, params_list)
            return

        async with self._write_lock:
            token = _write_lock_held_var.set(True)
            try:
                await self._do_execute_many(sql, params_list)
            finally:
                _write_lock_held_var.reset(token)

    async def _do_execute_many(self, sql: str, params_list: list[list]) -> None:
        """Internal batch execution logic."""
        try:
            await self._db.executemany(sql, params_list)
            await self._db.commit()
        except Exception as e:
            raise DatabaseError(f"Execute many failed: {e}") from e

    # ─── PUBLIC API: transaction() ────────────────────────
    #
    # Opens an explicit transaction using an async context manager.
    # Within the block, all operations share the same
    # connection and SQLite transaction (via SAVEPOINT).
    #
    # - Auto-COMMIT on block exit without errors.
    # - Auto-ROLLBACK if any exception occurs.
    #
    # Example:
    #   async with db.transaction() as tx:
    #       user_id = await tx.execute(
    #           "INSERT INTO users (name) VALUES ($1)", ["Ana"]
    #       )
    #       await tx.execute(
    #           "INSERT INTO profiles (user_id, bio) VALUES ($1, $2)",
    #           [user_id, "Hello!"]
    #       )
    #   # If any execute fails, everything is rolled back.
    #

    def transaction(self) -> Transaction:
        if self._db is None:
            raise DatabaseConnectionError("Cannot start transaction: connection is not initialized.")
        return Transaction(self._db, self._write_lock)

    # ─── PUBLIC API: health_check() ───────────────────────
    #
    # Verifies the connection is active and the DB responds.
    # Useful for the Registry and monitoring.
    #
    # Returns: bool
    #   - True if the connection works.
    #   - False if there's any error.
    #

    async def health_check(self) -> bool:
        try:
            if self._db is None:
                return False
            cursor = await self._db.execute("SELECT 1")
            await cursor.fetchone()
            return True
        except Exception:
            return False

    # ─── INTERFACE DESCRIPTION ────────────────────────────

    def get_interface_description(self) -> str:
        return """
        Async SQLite Persistence Tool (sqlite):
        - PURPOSE: Drop-in replacement for PostgreSQL. Lightweight relational data
          storage using SQLite with async access. Accepts PostgreSQL-style placeholders
          ($1, $2...) and converts them transparently to SQLite's native '?'.
        - PLACEHOLDERS: Use $1, $2, $3... (SAME as PostgreSQL — swap-compatible).
        - CAPABILITIES:
            - await query(sql, params?) → list[dict]: Read multiple rows (SELECT).
            - await query_one(sql, params?) → dict | None: Read a single row (SELECT).
            - await execute(sql, params?) → int | None: Write data (INSERT/UPDATE/DELETE).
              With RETURNING (SQLite 3.35+): returns the first column value.
              INSERT without RETURNING: returns lastrowid. Others: returns affected row count.
            - await execute_many(sql, params_list) → None: Batch writes.
            - async with transaction() as tx: Explicit transaction block with auto-commit/rollback.
              Inside tx: tx.query(), tx.query_one(), tx.execute() — same signatures.
            - await health_check() → bool: Verify database connectivity.
        - EXCEPTIONS: Raises DatabaseError or DatabaseConnectionError on failure.
        - MIGRATIONS: SQL files in domains/*/migrations/*.sql are auto-applied on boot via
          topological sort (alphabetical by default). To declare that one migration must
          run before another, add as the first comment line:
            "-- depends: other_domain/001_file.sql"
          Works for same-domain or cross-domain dependencies. .sql extension is optional.
        """

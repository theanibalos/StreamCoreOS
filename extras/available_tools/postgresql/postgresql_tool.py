"""
PostgreSQL Tool — Gold-Standard Database Contract for MicroCoreOS
=================================================================

This is the REFERENCE IMPLEMENTATION for database tools in MicroCoreOS.
Any new database tool (MySQL, MariaDB, etc.) MUST follow this contract.

PUBLIC CONTRACT (what plugins use):
─────────────────────────────────────────────
    rows  = await db.query("SELECT * FROM users WHERE age > $1", [18])
    row   = await db.query_one("SELECT * FROM users WHERE id = $1", [5])
    newid = await db.execute("INSERT INTO users (name) VALUES ($1) RETURNING id", ["Ana"])
    count = await db.execute("UPDATE users SET active = $1", [True])
    await db.execute_many("INSERT INTO logs (msg) VALUES ($1)", [["a"], ["b"]])

    async with db.transaction() as tx:
        uid = await tx.execute("INSERT INTO users (name) VALUES ($1) RETURNING id", ["Ana"])
        await tx.execute("INSERT INTO profiles (user_id) VALUES ($1)", [uid])
        # Auto-COMMIT on exit. Auto-ROLLBACK on exception.

    ok = await db.health_check()

PLACEHOLDERS: PostgreSQL uses $1, $2, $3... (NOT '?' like SQLite).
"""

import os
import asyncio
import asyncpg
from microcoreos.base_tool import BaseTool


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXCEPTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DatabaseError(Exception):
    """Generic database error. Wraps asyncpg exceptions."""
    pass


class DatabaseConnectionError(DatabaseError):
    """Connection error to the PostgreSQL server."""
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TRANSACTION CONTEXT MANAGER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Transaction:
    """
    Explicit transaction over a connection acquired from the pool.

    Usage:
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO ...", [...])
            await tx.execute("UPDATE ...", [...])
            rows = await tx.query("SELECT ...", [...])
        # Auto-COMMIT on block exit.
        # Auto-ROLLBACK if any exception occurs.

    The context manager handles:
    1. Acquiring a connection from the pool.
    2. Opening a real PostgreSQL transaction (BEGIN).
    3. COMMIT if everything succeeds.
    4. ROLLBACK if an exception occurs.
    5. Returning the connection to the pool ALWAYS.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool: asyncpg.Pool = pool
        self._conn: asyncpg.Connection | None = None
        self._tx: asyncpg.connection.transaction.Transaction | None = None

    async def __aenter__(self) -> "Transaction":
        try:
            self._conn = await self._pool.acquire()
            self._tx = self._conn.transaction()
            await self._tx.start()
        except asyncpg.PostgresError as e:
            # If acquisition or BEGIN fails, clean up and propagate
            if self._conn is not None:
                await self._pool.release(self._conn)
                self._conn = None
            raise DatabaseConnectionError(f"Failed to start transaction: {e}") from e
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type is None:
                # No errors → COMMIT
                await self._tx.commit()
            else:
                # Errors occurred → ROLLBACK
                await self._tx.rollback()
        finally:
            # ALWAYS return the connection to the pool
            if self._conn is not None:
                await self._pool.release(self._conn)
                self._conn = None
        # Do not suppress the exception (return False)
        return False

    # ─── API within the transaction ──────────────────────

    async def query(self, sql: str, params: list | None = None) -> list[dict]:
        """SELECT within the transaction. Returns list[dict]."""
        params = params or []
        try:
            rows = await self._conn.fetch(sql, *params)
            return [dict(row) for row in rows]
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Transaction query failed: {e}") from e

    async def query_one(self, sql: str, params: list | None = None) -> dict | None:
        """SELECT a single record within the transaction. Returns dict or None."""
        params = params or []
        try:
            row = await self._conn.fetchrow(sql, *params)
            return dict(row) if row is not None else None
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Transaction query_one failed: {e}") from e

    async def execute(self, sql: str, params: list | None = None) -> int | None:
        """
        INSERT/UPDATE/DELETE within the transaction.

        - If the SQL has RETURNING, returns the value of the first column
          of the first record (typically the generated ID).
        - If no RETURNING, returns the number of affected rows.
        """
        params = params or []
        try:
            # Try fetchrow first (for RETURNING)
            if "RETURNING" in sql.upper():
                row = await self._conn.fetchrow(sql, *params)
                if row is not None:
                    return row[0]
                return None
            else:
                result = await self._conn.execute(sql, *params)
                return _parse_affected_rows(result)
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Transaction execute failed: {e}") from e


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INTERNAL UTILITIES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_affected_rows(status: str) -> int:
    """
    Parses the asyncpg status string (e.g.: 'UPDATE 3', 'DELETE 1', 'INSERT 0 1')
    and extracts the number of affected rows.
    """
    try:
        parts = status.split()
        return int(parts[-1])
    except (ValueError, IndexError):
        print(f"[PostgresqlTool] Warning: could not parse affected rows from status: {status!r}")
        return 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# POSTGRESQL TOOL
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PostgresqlTool(BaseTool):
    """
    PostgreSQL persistence tool for MicroCoreOS.

    Uses asyncpg with a connection pool for high-performance,
    non-blocking database access. This is the gold-standard
    implementation that all database tools should follow.
    """

    # ─── IDENTITY ─────────────────────────────────────────

    @property
    def name(self) -> str:
        return "postgresql"

    # ─── CONSTRUCTOR ──────────────────────────────────────
    #
    # Configuration read only. Zero logic, zero I/O.
    # The pool is created in setup(), NOT here.
    #

    def __init__(self) -> None:
        self._host: str = os.getenv("PG_HOST", "localhost")
        self._port: int = int(os.getenv("PG_PORT", "5432"))
        self._user: str = os.getenv("PG_USER", "postgres")
        self._password: str = os.getenv("PG_PASSWORD", "")
        self._database: str = os.getenv("PG_DATABASE", "postgres")
        self._min_pool: int = int(os.getenv("PG_MIN_POOL", "1"))
        self._max_pool: int = int(os.getenv("PG_MAX_POOL", "10"))
        self._connect_timeout: float = float(os.getenv("PG_CONNECT_TIMEOUT", "5"))
        self._command_timeout: float = float(os.getenv("PG_COMMAND_TIMEOUT", "30"))
        self._pool: asyncpg.Pool | None = None

    # ─── LIFECYCLE: setup() ───────────────────────────────
    #
    # Infrastructure phase. Runs BEFORE plugins.
    # Responsibilities:
    #   1. Create the connection pool.
    #   2. Create the internal migration history table.
    #

    async def setup(self) -> None:
        print(f"[System] PostgresqlTool: Connecting to {self._host}:{self._port}/{self._database}...")

        try:
            self._pool = await asyncio.wait_for(
                asyncpg.create_pool(
                    host=self._host,
                    port=self._port,
                    user=self._user,
                    password=self._password,
                    database=self._database,
                    min_size=self._min_pool,
                    max_size=self._max_pool,
                    timeout=self._connect_timeout,
                    command_timeout=self._command_timeout,
                ),
                timeout=self._connect_timeout,
            )
        except asyncio.TimeoutError:
            raise DatabaseConnectionError(
                f"Timeout connecting to PostgreSQL at {self._host}:{self._port}/{self._database} "
                f"(>{self._connect_timeout}s)"
            )
        except (asyncpg.PostgresError, OSError, ConnectionRefusedError) as e:
            raise DatabaseConnectionError(
                f"Cannot connect to PostgreSQL at {self._host}:{self._port}/{self._database}: {e}"
            ) from e

        # Create internal migrations table
        await self.execute("""
            CREATE TABLE IF NOT EXISTS _migrations_history (
                id          SERIAL PRIMARY KEY,
                domain      TEXT NOT NULL,
                filename    TEXT NOT NULL,
                applied_at  TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(domain, filename)
            )
        """)

        print(f"[System] PostgresqlTool: Pool ready (min={self._min_pool}, max={self._max_pool}).")

    # ─── LIFECYCLE: on_boot_complete() ────────────────────
    #
    # Runs AFTER all tools and plugins are loaded.
    # Responsibility: execute pending SQL migrations.
    #
    # Migrations are searched in: domains/*/migrations/*.sql
    # Applied in TOPOLOGICAL ORDER based on "-- depends:" headers.
    # If no dependencies are declared, falls back to alphabetical.
    #
    # Dependency syntax (first lines of .sql file):
    #   -- depends: users/001_create_users_table
    #   -- depends: profiles/001_create_profiles_table
    #
    # Each migration runs in its own transaction.
    # If a migration fails, it is ROLLED BACK.
    #

    async def on_boot_complete(self, container) -> None:
        print("[System] PostgresqlTool: Checking for pending migrations...")
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
            async with self.transaction() as tx:
                statements = [s.strip() for s in sql_script.split(";") if s.strip()]
                for statement in statements:
                    await tx.execute(statement)

                # Register successful migration
                await tx.execute(
                    "INSERT INTO _migrations_history (domain, filename) VALUES ($1, $2)",
                    [domain, filename],
                )

            print(f"  [Migration] ✅ Applied {key}")

    # ─── LIFECYCLE: shutdown() ────────────────────────────
    #
    # Closes the connection pool in an orderly manner.
    # Waits for active connections to finish.
    #

    async def shutdown(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            print("[PostgresqlTool] Connection pool closed.")

    # ─── PUBLIC API: query() ──────────────────────────────
    #
    # Executes a SELECT and returns ALL records.
    #
    # Parameters:
    #   sql:    str           — SQL query with placeholders $1, $2...
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
        params = params or []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
                return [dict(row) for row in rows]
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Query failed: {e}") from e

    # ─── PUBLIC API: query_one() ──────────────────────────
    #
    # Executes a SELECT and returns the FIRST record or None.
    #
    # Parameters:
    #   sql:    str           — SQL query with placeholders $1, $2...
    #   params: list | None   — Values for the placeholders
    #
    # Returns: dict | None
    #   - None if no results.
    #   - dict with keys = column names.
    #
    # Example:
    #   user = await db.query_one("SELECT * FROM users WHERE id = $1", [5])
    #   # {"id": 5, "name": "Ana", "email": "ana@mail.com"} or None
    #

    async def query_one(self, sql: str, params: list | None = None) -> dict | None:
        params = params or []
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(sql, *params)
                return dict(row) if row is not None else None
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Query failed: {e}") from e

    # ─── PUBLIC API: execute() ────────────────────────────
    #
    # Executes INSERT, UPDATE or DELETE.
    #
    # Parameters:
    #   sql:    str           — SQL with placeholders $1, $2...
    #   params: list | None   — Values for the placeholders
    #
    # Returns: int | None
    #   - With RETURNING: the value of the first column of the first record
    #     (typically the generated ID).
    #   - Without RETURNING: the number of affected rows (int).
    #
    # Example with RETURNING:
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
        params = params or []
        try:
            async with self._pool.acquire() as conn:
                if "RETURNING" in sql.upper():
                    row = await conn.fetchrow(sql, *params)
                    if row is not None:
                        return row[0]
                    return None
                else:
                    result = await conn.execute(sql, *params)
                    return _parse_affected_rows(result)
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Execute failed: {e}") from e

    # ─── PUBLIC API: execute_many() ───────────────────────
    #
    # Executes the same SQL statement with multiple parameter sets.
    # Internally optimized by asyncpg (pipeline).
    #
    # Parameters:
    #   sql:         str         — SQL with placeholders $1, $2...
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
        try:
            async with self._pool.acquire() as conn:
                # asyncpg.executemany expects a list of tuples
                await conn.executemany(sql, [tuple(p) for p in params_list])
        except asyncpg.PostgresError as e:
            raise DatabaseError(f"Execute many failed: {e}") from e

    # ─── PUBLIC API: transaction() ────────────────────────
    #
    # Opens an explicit transaction using an async context manager.
    # Within the block, all operations share the same
    # PostgreSQL connection and transaction.
    #
    # - Auto-COMMIT on block exit without errors.
    # - Auto-ROLLBACK if any exception occurs.
    # - The connection is returned to the pool ALWAYS.
    #
    # Example:
    #   async with db.transaction() as tx:
    #       user_id = await tx.execute(
    #           "INSERT INTO users (name) VALUES ($1) RETURNING id", ["Ana"]
    #       )
    #       await tx.execute(
    #           "INSERT INTO profiles (user_id, bio) VALUES ($1, $2)",
    #           [user_id, "Hello!"]
    #       )
    #   # If any execute fails, everything is rolled back.
    #

    def transaction(self) -> Transaction:
        if self._pool is None:
            raise DatabaseConnectionError("Cannot start transaction: pool is not initialized.")
        return Transaction(self._pool)

    # ─── PUBLIC API: health_check() ───────────────────────
    #
    # Verifies that the pool is active and the DB responds.
    # Useful for the Registry and monitoring.
    #
    # Returns: bool
    #   - True if the connection works.
    #   - False if there is any error.
    #

    async def health_check(self) -> bool:
        try:
            if self._pool is None:
                return False
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # ─── INTERFACE DESCRIPTION ────────────────────────────

    def get_interface_description(self) -> str:
        return """
        Async PostgreSQL Persistence Tool (db):
        - PURPOSE: Production-grade relational data storage using PostgreSQL with connection pooling.
        - PLACEHOLDERS: Use $1, $2, $3... (NOT '?' like SQLite).
        - CAPABILITIES:
            - await query(sql, params?) → list[dict]: Read multiple rows (SELECT).
            - await query_one(sql, params?) → dict | None: Read a single row (SELECT).
            - await execute(sql, params?) → int | None: Write data (INSERT/UPDATE/DELETE).
              With RETURNING: returns the first column value. Without: returns affected row count.
            - await execute_many(sql, params_list) → None: Batch writes with optimized pipeline.
            - async with transaction() as tx: Explicit transaction block with auto-commit/rollback.
              Inside tx: tx.query(), tx.query_one(), tx.execute() — same signatures.
            - await health_check() → bool: Verify database connectivity.
        - EXCEPTIONS: Raises DatabaseError or DatabaseConnectionError on failure.
        """

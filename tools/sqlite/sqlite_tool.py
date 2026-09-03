"""
SQLite Tool — PostgreSQL-Compatible `db` Tool for MicroCoreOS
================================================================

100% COMPATIBLE with the PostgreSQL gold-standard contract AT THE
TOOL-API LEVEL: same methods, same PostgreSQL-style placeholders.
Plugins write PostgreSQL-style SQL ($1, $2...) and this tool
transparently converts placeholders to SQLite's native '?'.
SQL text itself is NEVER dialect-translated (see migration note below).

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

ERRORS (IDENTICAL to PostgreSQL — same classification, same swap):
─────────────────────────────────────────────────────────────────
    Every failure raises DatabaseError carrying `kind` from a CLOSED
    vocabulary (see ERROR_KINDS), plus best-effort `table` / `columns`:

        try:
            await db.execute("INSERT INTO users (email) VALUES ($1)", [email])
        except Exception as e:
            if getattr(e, "kind", None) == "unique_violation":
                ...

    Plugins branch on `kind`, NEVER on str(e): the message text is
    engine-specific ("UNIQUE constraint failed: users.email" here,
    'duplicate key value violates unique constraint "users_email_key"' on
    PostgreSQL), so text matching breaks silently on the swap.

PLACEHOLDERS: Plugins ALWAYS use $1, $2, $3... (PostgreSQL-style).
              This tool converts them internally to '?' for SQLite.
              That is the whole of what the swap gives you for free: no plugin
              changes a tool call, a signature or an error branch.

⚠ WHAT THE SWAP DOES *NOT* DO — THIS IS NOT AN ORM:
  Neither this tool nor the PostgreSQL tool translates SQL dialects. Every
  migration in domains/*/migrations/ and every query string in a plugin runs
  VERBATIM on whichever engine is active. The tool normalizes the placeholder
  and the error `kind`; the SQL you wrote is the SQL that executes.

  So the swap is cheap but not automatic, and that is the deliberate trade:
  no abstraction layer to maintain or fight, in exchange for one explicit
  review pass per swap. The pass is finite and fully enumerable precisely
  because nothing is generated or hidden — every table and every query is
  somewhere you can grep:

      domains/*/migrations/*.sql        every table
      domains/*/plugins/*_plugin.py     every query (db.query, db.query_one,
                                        db.execute, db.execute_many, tx.)
      every `except` around a db./tx.   engine wording differs; branch on
                                        `kind`, never on str(e)

  Engine-specific SQL is a valid choice — it commits you to that engine.
  Portable SQL (e.g. CURRENT_TIMESTAMP, not NOW()) keeps the swap free.
  Either way the review happens: docs/ELASTIC_DEPLOYMENT.md, Stage 1.
"""

import os
import re
import asyncio
import aiosqlite
from microcoreos import BaseTool

from tools.sqlite.errors import DatabaseError, DatabaseConnectionError, _classify_error
from tools.sqlite.transaction import Transaction, _normalize_sql, _normalize_sql_many, _write_lock_held_var
from tools.sqlite.migrations import run_migrations


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ERRORS, TRANSACTION, MIGRATIONS — split into sibling modules
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# tools/sqlite/errors.py       — DatabaseError, DatabaseConnectionError,
#                                 ERROR_KINDS, error-message classification.
# tools/sqlite/transaction.py  — Transaction, placeholder normalization
#                                 (_normalize_sql, _normalize_sql_many), and
#                                 the write-lock reentrancy ContextVar.
# tools/sqlite/migrations.py   — run_migrations(tool), invoked from setup().
#
# DatabaseError and _normalize_sql are re-exported above (imported into this
# module's namespace) because external code imports them from
# tools.sqlite.sqlite_tool, not from their new module — see tests/tools/sqlite/test_sqlite_tool.py
# and tests/tools/sqlite/test_sqlite_concurrency.py.
#


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COLUMN TYPE NORMALIZATION (describe_schema contract)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Closed vocabulary shared with the PostgreSQL tool: any engine type not
# recognized here falls back to "text". Checked in this order (a category
# earlier in the list wins if a type string happens to start with more than
# one prefix, which doesn't occur for the prefixes below but keeps the match
# deterministic).
_TYPE_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("int", ("BIGINT", "BIGSERIAL", "SMALLINT", "INTEGER", "INT", "SERIAL")),
    ("float", ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")),
    ("bool", ("BOOL", "BOOLEAN")),
    ("timestamp", ("TIMESTAMPTZ", "TIMESTAMP", "DATETIME", "DATE", "TIME")),
    ("json", ("JSONB", "JSON")),
    ("blob", ("BLOB", "BYTEA")),
]

def _normalize_column_type(raw_type: str) -> str:
    """
    Maps the type string as written in CREATE TABLE (what PRAGMA table_info
    reports verbatim) to the closed vocabulary:
    text/int/float/bool/timestamp/json/blob. Strips any "(n)" precision
    suffix and matches by prefix, case-insensitively. Anything unrecognized
    (VARCHAR, CHAR, TEXT, ...) maps to "text".
    """
    bare = re.sub(r"\(.*\)", "", raw_type or "").strip().upper()
    for category, prefixes in _TYPE_PREFIXES:
        if any(bare.startswith(prefix) for prefix in prefixes):
            return category
    return "text"


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

        await self._run_migrations()

    # ─── MIGRATIONS: run from setup(), NOT from on_boot_complete() ──
    #
    # The algorithm and the WHY-setup()/topological-order/per-migration-
    # transaction rationale live in tools/sqlite/migrations.py::run_migrations —
    # moved there verbatim, this is just the call site.
    #

    async def _run_migrations(self) -> None:
        await run_migrations(self)

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
                async with self._db.execute(sql, params) as cursor:
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    rows = await cursor.fetchall()
                    return [dict(zip(columns, row)) for row in rows]
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) or "database is busy" in str(e):
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                raise DatabaseError(f"Query failed: {e}", **_classify_error(e)) from e
            except Exception as e:
                raise DatabaseError(f"Query failed: {e}", **_classify_error(e)) from e

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
                async with self._db.execute(sql, params) as cursor:
                    columns = [desc[0] for desc in cursor.description] if cursor.description else []
                    row = await cursor.fetchone()
                    return dict(zip(columns, row)) if row is not None else None
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) or "database is busy" in str(e):
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                raise DatabaseError(f"Query failed: {e}", **_classify_error(e)) from e
            except Exception as e:
                raise DatabaseError(f"Query failed: {e}", **_classify_error(e)) from e

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
            # Nested inside an active db.transaction(): join it instead of
            # committing here. SQLite's COMMIT closes the WHOLE underlying
            # transaction regardless of how many SAVEPOINTs are open, so a
            # commit at this point would silently finalize the outer
            # transaction early — the outer block's later ROLLBACK TO
            # SAVEPOINT (on a subsequent failure) would then have nothing
            # left to undo, breaking atomicity for every statement it already
            # ran. The outer Transaction.__aexit__ owns commit/rollback here.
            return await self._do_execute(sql, params, commit=False)

        async with self._write_lock:
            token = _write_lock_held_var.set(True)
            try:
                return await self._do_execute(sql, params, commit=True)
            finally:
                _write_lock_held_var.reset(token)

    async def _do_execute(self, sql: str, params: list | None, commit: bool = True) -> int | None:
        """Internal execution logic with retry capability.

        commit=False when called from within an already-open outer
        transaction (see the reentrancy branch in execute()) — the caller
        owns finalizing the transaction in that case.
        """
        for attempt in range(3):
            try:
                if re.search(r"\bRETURNING\b", sql.upper()):
                    async with self._db.execute(sql, params) as cursor:
                        row = await cursor.fetchone()
                    if commit:
                        await self._db.commit()
                    if row is not None:
                        return row[0]
                    return None
                else:
                    async with self._db.execute(sql, params) as cursor:
                        # Read while the cursor is open: closing invalidates both.
                        result = (cursor.lastrowid
                                  if sql.strip().upper().startswith("INSERT")
                                  else cursor.rowcount)
                    if commit:
                        await self._db.commit()
                    return result
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) or "database is busy" in str(e):
                    if attempt < 2:
                        await asyncio.sleep(0.05 * (attempt + 1))
                        continue
                raise DatabaseError(f"Execute failed: {e}", **_classify_error(e)) from e
            except Exception as e:
                raise DatabaseError(f"Execute failed: {e}", **_classify_error(e)) from e

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
            # See execute()'s reentrancy branch: join the outer transaction
            # instead of committing here.
            await self._do_execute_many(sql, params_list, commit=False)
            return

        async with self._write_lock:
            token = _write_lock_held_var.set(True)
            try:
                await self._do_execute_many(sql, params_list, commit=True)
            finally:
                _write_lock_held_var.reset(token)

    async def _do_execute_many(self, sql: str, params_list: list[list], commit: bool = True) -> None:
        """Internal batch execution logic.

        commit=False when called from within an already-open outer
        transaction — the caller owns finalizing it in that case.
        """
        try:
            await self._db.executemany(sql, params_list)
            if commit:
                await self._db.commit()
        except Exception as e:
            raise DatabaseError(f"Execute many failed: {e}", **_classify_error(e)) from e

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
            async with self._db.execute("SELECT 1") as cursor:
                await cursor.fetchone()
            return True
        except Exception:
            return False

    # ─── PUBLIC API: describe_schema() ────────────────────
    #
    # Introspects the live schema of the active database, normalized to the
    # same closed vocabulary and shape the PostgreSQL tool produces, so the
    # same migration yields an identical description on either engine.
    #
    # Returns: dict
    #   {table_name: {"internal": bool, "columns": [...], "unique": [...],
    #                 "foreign_keys": [...]}}
    #

    async def describe_schema(self) -> dict:
        try:
            # sqlite_% covers sqlite_master, sqlite_sequence, sqlite_stat*...
            # engine-owned, never surfaced (not even as internal).
            tables = await self.query(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )

            schema: dict = {}
            for row in tables:
                table_name = row["name"]

                # PRAGMA statements don't accept placeholders; table_name is
                # never external input here, it comes straight out of
                # sqlite_master, so interpolation is safe.
                columns_info = await self.query(f"PRAGMA table_info({table_name})")
                columns = [
                    {
                        "name": col["name"],
                        "type": _normalize_column_type(col["type"]),
                        # A PRIMARY KEY is reported NOT nullable regardless of what
                        # PRAGMA says. SQLite reports notnull=0 for an INTEGER
                        # PRIMARY KEY (rowid alias) and even tolerates NULL in a
                        # TEXT PRIMARY KEY — both are documented legacy quirks, not
                        # schema intent. PostgreSQL makes every PK column NOT NULL,
                        # so normalizing here is what keeps describe_schema()
                        # identical across engines (see the cross-engine test in
                        # tests/tools/db/test_db_parity.py).
                        "nullable": not col["notnull"] and not col["pk"],
                        "default": col["dflt_value"],
                        "primary_key": col["pk"] > 0,
                    }
                    # table_info already returns columns in physical (cid) order.
                    for col in columns_info
                ]

                # UNIQUE constraints: index_list gives every index on the
                # table; origin != 'pk' drops the implicit PK index (already
                # captured per-column above, not repeated here).
                index_list = await self.query(f"PRAGMA index_list({table_name})")
                unique: list[list[str]] = []
                for idx in index_list:
                    if not idx["unique"] or idx["origin"] == "pk":
                        continue
                    index_columns = await self.query(f"PRAGMA index_info({idx['name']})")
                    # index_info rows aren't guaranteed pre-sorted; seqno is
                    # the declared column order within the constraint.
                    ordered = sorted(index_columns, key=lambda c: c["seqno"])
                    unique.append([c["name"] for c in ordered])
                unique.sort(key=lambda cols: cols[0])

                fk_list = await self.query(f"PRAGMA foreign_key_list({table_name})")
                foreign_keys = [
                    {
                        "column": fk["from"],
                        "references_table": fk["table"],
                        "references_column": fk["to"],
                    }
                    # Compound FKs share an "id" across rows in the PRAGMA
                    # output; the contract wants them flattened, one entry
                    # per column, so no grouping by id here.
                    for fk in fk_list
                ]

                schema[table_name] = {
                    "internal": table_name.startswith("_"),
                    "columns": columns,
                    "unique": unique,
                    "foreign_keys": foreign_keys,
                }

            return dict(sorted(schema.items()))
        except Exception as e:
            raise DatabaseConnectionError(f"Failed to describe schema: {e}") from e

    # ─── INTERFACE DESCRIPTION ────────────────────────────

    def get_interface_description(self) -> str:
        return """
        Async SQLite Persistence Tool (sqlite):
        - PURPOSE: PostgreSQL-compatible relational storage (drop-in swap at the
          TOOL-API level: same methods, same placeholders). Accepts PostgreSQL-style
          placeholders ($1, $2...) and converts them transparently to SQLite's
          native '?'. SQL text itself is NEVER dialect-translated.
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
            - await describe_schema() → dict: Live schema of the active database:
              {table: {internal, columns, unique, foreign_keys}}.
              Column types are normalized to a closed vocabulary
              (text/int/float/bool/timestamp/json/blob) so the same migration
              yields the same description on any engine.
              Tables whose name starts with "_" are marked internal;
              engine-owned tables are excluded.
        - EXCEPTIONS: Raises DatabaseError or DatabaseConnectionError on failure.
          Every DatabaseError carries a CLASSIFIED, engine-independent contract:
            - kind: one of unique_violation / foreign_key_violation /
              not_null_violation / check_violation / unknown (CLOSED vocabulary —
              the same values on any engine, so the swap keeps behavior).
            - table / columns: the target of the violation, filled in only where
              every engine can report it (unique and NOT NULL); FOREIGN KEY and
              CHECK carry kind only.
          Branch on the kind, NEVER on str(e) — the message text is engine-specific:
            except Exception as e:
                if getattr(e, "kind", None) == "unique_violation": ...
        - MIGRATIONS: SQL files in domains/*/migrations/*.sql are auto-applied on boot via
          topological sort (alphabetical by default). Migrations run VERBATIM (no
          dialect translation). Engine-specific SQL commits you to that engine;
          portable SQL (e.g. CURRENT_TIMESTAMP, not NOW()) keeps the
          SQLite <-> PostgreSQL swap free. To declare that one migration must
          run before another, add as the first comment line:
            "-- depends: other_domain/001_file.sql"
          Works for same-domain or cross-domain dependencies. .sql extension is optional.
        """

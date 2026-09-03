"""
SQLite Tool — Placeholder Normalization and Transaction Context Manager
================================================================

Moved out of sqlite_tool.py (mechanical split, zero behavior change).
See tools/sqlite/sqlite_tool.py for the public `db` tool contract this
is part of.

`_normalize_sql` is re-exported from tools/sqlite/sqlite_tool.py because
external code imports it from there — see the import block at the top of
that file.
"""

import re
import uuid
import asyncio
import aiosqlite
from contextvars import ContextVar

from tools.sqlite.errors import DatabaseError, DatabaseConnectionError, _classify_error


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONTEXT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Tracks if the current task holds the SQLite write lock to allow reentrancy
_write_lock_held_var: ContextVar[bool] = ContextVar("sqlite_write_lock_held", default=False)


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
            raise DatabaseError(f"Transaction query failed: {e}", **_classify_error(e)) from e

    async def query_one(self, sql: str, params: list | None = None) -> dict | None:
        """SELECT a single record within the transaction. Returns dict or None."""
        sql, params = _normalize_sql(sql, params)
        try:
            cursor = await self._db.execute(sql, params)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            row = await cursor.fetchone()
            return dict(zip(columns, row)) if row is not None else None
        except Exception as e:
            raise DatabaseError(f"Transaction query_one failed: {e}", **_classify_error(e)) from e

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
            raise DatabaseError(f"Transaction execute failed: {e}", **_classify_error(e)) from e

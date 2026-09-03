"""
SQLite Tool — Error Types and Classification
================================================================

Moved out of sqlite_tool.py (mechanical split, zero behavior change).
Holds the exception classes and the engine-specific message-parsing that
maps aiosqlite failures onto the CLOSED error-kind vocabulary shared with
the PostgreSQL tool. See tools/sqlite/sqlite_tool.py for the public
`db` tool contract these errors are part of.

`DatabaseError` and `_normalize_sql` (in transaction.py) are re-exported
from tools/sqlite/sqlite_tool.py because external code imports them from
there — see the import block at the top of that file.
"""

from microcoreos import ToolUnavailableError

import re


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# EXCEPTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Error-kind vocabulary — CLOSED, shared VERBATIM with the PostgreSQL tool.
# Same idea as the describe_schema type vocabulary, applied to failures: each
# engine reports constraint violations its own way (SQLite: message text,
# PostgreSQL: SQLSTATE), so the tool classifies and every db tool exposes the
# SAME set of values. Adding a value here means adding it to EVERY db tool.
ERROR_KINDS = (
    "unique_violation",
    "foreign_key_violation",
    "not_null_violation",
    "check_violation",
    "unknown",
)


class DatabaseError(Exception):
    """Generic database error. Wraps aiosqlite exceptions.

    Carries the engine-independent classification of the failure:

        kind:    one of ERROR_KINDS — ALWAYS present.
        table:   table the violation happened on, or None.
        columns: tuple of column names involved, possibly empty.

    Plugins branch on `kind`, NEVER on str(e) — the message text is
    engine-specific and changes under your feet on an engine swap:

        except Exception as e:
            if getattr(e, "kind", None) == "unique_violation":
                return {"success": False, "error": "Email already in use"}

    Duck-typed on purpose: a plugin CANNOT import this class (importing from
    tools/ is an architecture violation — see the architecture linter), so the
    contract is "an exception carrying these attributes", which any db tool
    satisfies without plugins knowing which engine is active.

    `table`/`columns` are populated ONLY where EVERY supported engine can
    supply them: unique and NOT NULL violations. SQLite reports no target
    whatsoever for FOREIGN KEY / CHECK failures ("FOREIGN KEY constraint
    failed", full stop), so those carry `kind` only on BOTH engines — better a
    field that is always empty than one that exists on PostgreSQL and silently
    vanishes after a swap. The raw engine message stays in str(e) for logs.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "unknown",
        table: str | None = None,
        columns: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.table = table
        self.columns = tuple(columns)


class DatabaseConnectionError(DatabaseError, ToolUnavailableError):
    """Connection error to the SQLite file.

    Inherits ToolUnavailableError so ToolProxy marks the tool DEAD immediately
    (infrastructure failure), unlike plain DatabaseError (likely business error).
    """
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ERROR CLASSIFICATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#
# SQLite reports constraint failures ONLY as message text (no error codes
# reachable through sqlite3/aiosqlite). The formats below are stable and
# English regardless of locale:
#
#   "UNIQUE constraint failed: users.email"       (multi-column: ", "-joined)
#   "NOT NULL constraint failed: users.name"
#   "FOREIGN KEY constraint failed"               (never carries a target)
#   "CHECK constraint failed: age_positive"       (constraint name, not a column)
#

_UNIQUE_RE = re.compile(r"UNIQUE constraint failed:\s*(?P<targets>[^\n]+)")
_NOT_NULL_RE = re.compile(r"NOT NULL constraint failed:\s*(?P<targets>[^\n]+)")
_FOREIGN_KEY_RE = re.compile(r"FOREIGN KEY constraint failed")
_CHECK_RE = re.compile(r"CHECK constraint failed")


def _parse_targets(raw: str) -> tuple[str | None, tuple[str, ...]]:
    """'users.email, users.tenant' → ('users', ('email', 'tenant'))."""
    table: str | None = None
    columns: list[str] = []
    for target in raw.split(","):
        target = target.strip()
        if not target:
            continue
        if "." in target:
            target_table, _, column = target.rpartition(".")
            table = table or target_table
            columns.append(column)
        else:
            columns.append(target)
    return table, tuple(columns)


def _classify_error(exc: Exception) -> dict:
    """Maps a sqlite3/aiosqlite exception to the shared error contract.

    Returns the kwargs for DatabaseError. Unrecognized failures map to
    "unknown" — the contract never guesses.
    """
    message = str(exc)

    match = _UNIQUE_RE.search(message)
    if match:
        table, columns = _parse_targets(match.group("targets"))
        return {"kind": "unique_violation", "table": table, "columns": columns}

    match = _NOT_NULL_RE.search(message)
    if match:
        table, columns = _parse_targets(match.group("targets"))
        return {"kind": "not_null_violation", "table": table, "columns": columns}

    if _FOREIGN_KEY_RE.search(message):
        return {"kind": "foreign_key_violation"}

    if _CHECK_RE.search(message):
        return {"kind": "check_violation"}

    return {"kind": "unknown"}

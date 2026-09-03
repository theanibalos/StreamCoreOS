"""
SQLite Tool — Migration Runner
================================================================

Moved out of sqlite_tool.py (mechanical split, zero behavior change).
`run_migrations(tool)` is called from SqliteTool.setup() with the
SqliteTool instance itself as `tool` (was `self` before the split), so it
can reuse the tool's query_one()/transaction()/_db exactly as before.
See tools/sqlite/sqlite_tool.py for the public `db` tool contract this
is part of, and for WHY migrations run from setup().
"""

import os
import re

from tools.sqlite.errors import DatabaseError, _classify_error

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?", re.IGNORECASE
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MIGRATIONS: run from setup(), NOT from on_boot_complete() ──
#
# Responsibility: execute pending SQL migrations.
#
# WHY setup(): the Kernel awaits EVERY tool's setup() together
# (asyncio.gather) before plugins boot and before any on_boot_complete
# runs. Migrating here means anything that reads the schema afterwards —
# a plugin in on_boot(), the manifest generator in on_boot_complete() —
# is guaranteed to see the migrated database.
# In on_boot_complete the order BETWEEN tools is os.walk order, i.e. a
# coin flip: a reader could run before the migrator and silently observe
# the previous schema. Not a hazard to manage — one to remove.
#
# Migrations are located in: domains/*/migrations/*.sql
# Applied in topological order (`-- depends:`), each within its own
# transaction. If a migration fails, that migration is rolled back and
# execution stops (raise) to prevent an inconsistent state. Raising from
# setup() registers the tool as FAIL in the registry — a broken migration
# is now visible in /system/status instead of a printed line.
#
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

async def run_migrations(tool) -> None:
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

    live_schema = set((await tool.describe_schema()).keys())

    # ── 3. Apply in topological order ───────────────────────────────
    for key in ordered_keys:
        if key not in migrations:
            continue  # dependency references a migration that doesn't exist (yet)

        info = migrations[key]
        domain = info["domain"]
        filename = info["filename"]

        with open(info["path"], "r", encoding="utf-8") as f:
            lines = f.readlines()
            sql_script = "\n".join(line for line in lines if not line.strip().startswith("--"))

        declared_tables = _CREATE_TABLE_RE.findall(sql_script)

        # Check if already applied
        already_applied = await tool.query_one(
            "SELECT 1 FROM _migrations_history WHERE domain = $1 AND filename = $2",
            [domain, filename],
        )
        if already_applied:
            missing_tables = [t for t in declared_tables if t not in live_schema]
            if not missing_tables:
                continue
            print(f"  [Migration] ⚠️ Table(s) {missing_tables} declared in {key} missing from DB despite history record. Repairing...")
            await tool._db.execute(
                "DELETE FROM _migrations_history WHERE domain = ? AND filename = ?",
                [domain, filename],
            )

        print(f"  [Migration] Applying {key}...")

        # Each migration in its own transaction
        try:
            async with tool.transaction():
                # Manually split and execute to ensure atomicity via our Transaction CM
                # This handles triggers if we are careful, but for now we split by ';'
                # which is what the user originally had but now inside our safe TX.
                statements = [s.strip() for s in sql_script.split(";") if s.strip()]
                for statement in statements:
                    await tool._db.execute(statement)

                # Register successful migration
                await tool._db.execute(
                    "INSERT INTO _migrations_history (domain, filename) VALUES (?, ?)",
                    [domain, filename],
                )
                # transaction __aexit__ will COMMIT
        except Exception as e:
            # transaction __aexit__ will ROLLBACK
            raise DatabaseError(f"Migration failed for {key}: {e}", **_classify_error(e)) from e

        live_schema.update(declared_tables)
        print(f"  [Migration] ✅ Applied {key}")

-- Durable one-shots store (Issue 19): rows are pending scheduled events.
-- Portable DDL: works as-is on SQLite and PostgreSQL.
CREATE TABLE IF NOT EXISTS scheduler_one_shots (
    job_id        TEXT PRIMARY KEY,
    run_at_epoch  DOUBLE PRECISION NOT NULL,
    event         TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhooks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    url            TEXT    NOT NULL,
    method         TEXT    NOT NULL DEFAULT 'POST',
    headers        TEXT,    -- JSON object
    body_template  TEXT,    -- Template with {user}, {data}, etc.
    trigger_type   TEXT    NOT NULL, -- 'command' or 'event'
    trigger_value  TEXT    NOT NULL, -- command name or event bus topic
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

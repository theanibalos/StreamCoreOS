CREATE TABLE IF NOT EXISTS ai_config (
    id           INTEGER PRIMARY KEY CHECK (id = 1),  -- single-row table
    provider     TEXT    NOT NULL DEFAULT 'openai',
    endpoint_url TEXT    NOT NULL DEFAULT '',
    api_key      TEXT    NOT NULL DEFAULT '',
    model        TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

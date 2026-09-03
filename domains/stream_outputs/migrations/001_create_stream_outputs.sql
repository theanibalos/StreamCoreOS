CREATE TABLE IF NOT EXISTS stream_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    overlay_id INTEGER,
    rtmp_url TEXT,
    stream_key_secret TEXT,
    status TEXT NOT NULL DEFAULT 'stopped',
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS youtube_tokens (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id     TEXT NOT NULL UNIQUE,
    channel_title  TEXT NOT NULL,
    access_token   TEXT NOT NULL,
    refresh_token  TEXT,
    scopes         TEXT NOT NULL DEFAULT '[]',
    expires_at     TEXT NOT NULL,
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);

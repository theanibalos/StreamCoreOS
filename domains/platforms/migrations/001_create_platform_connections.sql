CREATE TABLE IF NOT EXISTS platform_connections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    platform            TEXT    NOT NULL,
    channel_id          TEXT    NOT NULL,
    channel_name        TEXT    NOT NULL,
    enabled             INTEGER NOT NULL DEFAULT 1,
    chat_read_enabled   INTEGER NOT NULL DEFAULT 1,
    chat_write_enabled  INTEGER NOT NULL DEFAULT 1,
    moderation_enabled  INTEGER NOT NULL DEFAULT 0,
    capabilities        TEXT    NOT NULL DEFAULT '{}',
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE(platform, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_platform_connections_platform ON platform_connections(platform);

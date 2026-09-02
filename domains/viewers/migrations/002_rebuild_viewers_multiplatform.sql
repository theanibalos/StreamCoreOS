DROP TABLE IF EXISTS viewers;

CREATE TABLE viewers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    global_user_id   TEXT    NOT NULL UNIQUE,
    platform         TEXT    NOT NULL,
    platform_user_id TEXT    NOT NULL,
    login            TEXT,
    display_name     TEXT    NOT NULL,
    avatar_url       TEXT,
    points           INTEGER NOT NULL DEFAULT 0,
    total_earned     INTEGER NOT NULL DEFAULT 0,
    is_regular       INTEGER NOT NULL DEFAULT 0,
    first_seen       TEXT    DEFAULT (datetime('now')),
    last_seen        TEXT    DEFAULT (datetime('now')),
    UNIQUE(platform, platform_user_id)
);

CREATE INDEX IF NOT EXISTS idx_viewers_login ON viewers(login);
CREATE INDEX IF NOT EXISTS idx_viewers_regular ON viewers(is_regular);

CREATE TABLE IF NOT EXISTS subscribers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    twitch_id         TEXT    UNIQUE NOT NULL,
    login             TEXT    NOT NULL,
    display_name      TEXT    NOT NULL,
    tier              TEXT    NOT NULL DEFAULT '1000',
    is_prime          INTEGER NOT NULL DEFAULT 0,
    is_gift           INTEGER NOT NULL DEFAULT 0,
    cumulative_months INTEGER NOT NULL DEFAULT 1,
    streak_months     INTEGER,
    subscribed_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    last_sub_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    is_active         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS viewer_bits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    twitch_id     TEXT    UNIQUE NOT NULL,
    login         TEXT    NOT NULL,
    display_name  TEXT    NOT NULL,
    bits_total    INTEGER NOT NULL DEFAULT 0,
    last_cheer_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscription_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    twitch_id       TEXT    NOT NULL,
    login           TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    event_type      TEXT    NOT NULL, -- subscribe | resub | sub_end | tier_change
    tier            TEXT,
    previous_tier   TEXT,
    cumulative_months INTEGER,
    streak_months   INTEGER,
    is_gift         INTEGER NOT NULL DEFAULT 0,
    gifter_id       TEXT,
    gifter_login    TEXT,
    gifter_display_name TEXT,
    event_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gifters (
    twitch_id       TEXT    PRIMARY KEY,
    login           TEXT    NOT NULL,
    display_name    TEXT    NOT NULL,
    gifts_total     INTEGER NOT NULL DEFAULT 0,
    last_gift_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sub_events_twitch_id ON subscription_events(twitch_id);
CREATE INDEX IF NOT EXISTS idx_sub_events_type      ON subscription_events(event_type);
CREATE INDEX IF NOT EXISTS idx_sub_events_at        ON subscription_events(event_at);

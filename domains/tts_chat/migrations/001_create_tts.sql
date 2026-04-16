-- TTS user voice assignments
CREATE TABLE IF NOT EXISTS tts_user_voice (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    twitch_id    TEXT NOT NULL UNIQUE,
    twitch_login TEXT NOT NULL,
    voice_id     TEXT NOT NULL,
    voice_name   TEXT NOT NULL,
    provider     TEXT NOT NULL DEFAULT 'edge_tts',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- TTS global settings (single row, id=1)
CREATE TABLE IF NOT EXISTS tts_settings (
    id                  INTEGER PRIMARY KEY DEFAULT 1,
    enabled             INTEGER NOT NULL DEFAULT 1,
    provider            TEXT    NOT NULL DEFAULT 'edge_tts',
    host                TEXT    NOT NULL DEFAULT 'localhost',
    port                INTEGER NOT NULL DEFAULT 17493,
    default_voice       TEXT    NOT NULL DEFAULT 'es-ES-AlvaroNeural',
    timeout_s           INTEGER NOT NULL DEFAULT 10,
    max_message_length  INTEGER NOT NULL DEFAULT 200,
    skip_commands       INTEGER NOT NULL DEFAULT 1,
    skip_links          INTEGER NOT NULL DEFAULT 1,
    sub_only            INTEGER NOT NULL DEFAULT 0,
    cooldown_seconds    INTEGER NOT NULL DEFAULT 0,
    blocked_words       TEXT    NOT NULL DEFAULT '[]',
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Seed default settings row
INSERT OR IGNORE INTO tts_settings (id) VALUES (1);

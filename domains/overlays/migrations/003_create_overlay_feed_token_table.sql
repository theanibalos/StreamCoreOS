-- Persists the channel overlay feed token: opaque, revocable, read-only,
-- scoped to /api/overlays/feed. Single-row (CHECK id = 1) — this is a
-- single-broadcaster deployment. Regenerating the token replaces the row,
-- which instantly invalidates every overlay still using the old one
-- (Twitch Alerts' "reset URL" behaviour). Kept out of overlay_vars on
-- purpose: that table is broadcast to overlays, a secret must never be.
CREATE TABLE IF NOT EXISTS overlay_feed_token (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

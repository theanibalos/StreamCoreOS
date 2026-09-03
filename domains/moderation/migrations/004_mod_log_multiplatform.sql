ALTER TABLE mod_log ADD COLUMN platform TEXT NOT NULL DEFAULT 'twitch';
ALTER TABLE mod_log ADD COLUMN channel_id TEXT;
ALTER TABLE mod_log ADD COLUMN user_id TEXT NOT NULL DEFAULT '';

UPDATE mod_log SET user_id = twitch_id WHERE user_id = '';

CREATE INDEX IF NOT EXISTS idx_mod_log_platform_channel ON mod_log(platform, channel_id);
CREATE INDEX IF NOT EXISTS idx_mod_log_user_id ON mod_log(user_id);

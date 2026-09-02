ALTER TABLE chat_log ADD COLUMN platform TEXT NOT NULL DEFAULT 'twitch';
ALTER TABLE chat_log ADD COLUMN source_message_id TEXT;

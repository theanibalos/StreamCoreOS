ALTER TABLE ai_config ADD COLUMN chat_system_prompt TEXT NOT NULL DEFAULT 'You are a helpful Twitch chat assistant. Be concise and reply in under 40 words.';
ALTER TABLE ai_config ADD COLUMN chat_max_tokens    INTEGER NOT NULL DEFAULT 200;
ALTER TABLE ai_config ADD COLUMN chat_temperature   REAL    NOT NULL DEFAULT 0.7;

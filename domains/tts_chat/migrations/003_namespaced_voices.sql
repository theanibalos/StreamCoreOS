-- Namespace default_voice in settings using the stored provider prefix
UPDATE tts_settings
SET default_voice = provider || ':' || default_voice
WHERE default_voice NOT LIKE '%:%';

-- Namespace voice_ids in user assignments using the stored provider column
UPDATE tts_user_voice
SET voice_id = provider || ':' || voice_id
WHERE voice_id NOT LIKE '%:%';

-- Drop provider-specific connection columns from tts_settings
-- (host, port, timeout_s, provider are now sourced from env vars only)
ALTER TABLE tts_settings DROP COLUMN provider;
ALTER TABLE tts_settings DROP COLUMN host;
ALTER TABLE tts_settings DROP COLUMN port;
ALTER TABLE tts_settings DROP COLUMN timeout_s;

-- Drop redundant provider column from tts_user_voice
-- (provider is now encoded in the voice_id prefix)
ALTER TABLE tts_user_voice DROP COLUMN provider;

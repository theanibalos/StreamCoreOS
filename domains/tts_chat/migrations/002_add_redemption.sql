-- Add redemption support to TTS settings
ALTER TABLE tts_settings ADD COLUMN redemption_title TEXT NOT NULL DEFAULT '';
ALTER TABLE tts_settings ADD COLUMN mod_bypass       INTEGER NOT NULL DEFAULT 1;

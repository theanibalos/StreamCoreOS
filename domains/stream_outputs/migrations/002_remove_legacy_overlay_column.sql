-- Remove the obsolete overlay reference without changing stream destinations.
ALTER TABLE stream_outputs DROP COLUMN overlay_id;

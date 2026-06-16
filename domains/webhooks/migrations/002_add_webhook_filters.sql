-- Migración autoritativa para reflejar el estado actual de la tabla webhooks
ALTER TABLE webhooks ADD COLUMN filter_field TEXT;
ALTER TABLE webhooks ADD COLUMN filter_value TEXT;

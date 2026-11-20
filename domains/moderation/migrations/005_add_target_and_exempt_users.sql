-- depends: moderation/003_add_exempt_roles.sql
ALTER TABLE mod_rules ADD COLUMN target_users TEXT NOT NULL DEFAULT '';
ALTER TABLE mod_rules ADD COLUMN exempt_users TEXT NOT NULL DEFAULT '';

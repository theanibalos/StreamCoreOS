-- Roles that bypass this specific rule, comma-separated (e.g. "mod,vip,regular,sub").
-- Broadcaster is always exempt from every rule and is not stored here.
ALTER TABLE mod_rules ADD COLUMN exempt_roles TEXT NOT NULL DEFAULT '';

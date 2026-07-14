CREATE TABLE IF NOT EXISTS ai_providers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    provider          TEXT    NOT NULL,
    endpoint_url      TEXT    NOT NULL,
    api_key           TEXT    NOT NULL DEFAULT '',
    model             TEXT    NOT NULL,
    timeout_s         INTEGER NOT NULL DEFAULT 120,
    disable_reasoning INTEGER NOT NULL DEFAULT 0,
    extra_headers     TEXT    NOT NULL DEFAULT '{}',
    extra_payload     TEXT    NOT NULL DEFAULT '{}',
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

ALTER TABLE ai_config ADD COLUMN active_provider_id INTEGER REFERENCES ai_providers(id);

INSERT INTO ai_providers (name, provider, endpoint_url, api_key, model, timeout_s, disable_reasoning, extra_headers, extra_payload)
SELECT
    'Proveedor migrado (' || provider || ')',
    provider, endpoint_url, api_key, model, timeout_s, disable_reasoning, extra_headers, extra_payload
FROM ai_config
WHERE id = 1 AND endpoint_url != '';

UPDATE ai_config
SET active_provider_id = (SELECT id FROM ai_providers ORDER BY id DESC LIMIT 1)
WHERE id = 1 AND EXISTS (SELECT 1 FROM ai_providers);

ALTER TABLE ai_config DROP COLUMN provider;
ALTER TABLE ai_config DROP COLUMN endpoint_url;
ALTER TABLE ai_config DROP COLUMN api_key;
ALTER TABLE ai_config DROP COLUMN model;
ALTER TABLE ai_config DROP COLUMN timeout_s;
ALTER TABLE ai_config DROP COLUMN disable_reasoning;
ALTER TABLE ai_config DROP COLUMN extra_headers;
ALTER TABLE ai_config DROP COLUMN extra_payload;

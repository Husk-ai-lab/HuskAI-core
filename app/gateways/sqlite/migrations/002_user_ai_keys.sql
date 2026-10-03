-- Per-user AI key (simplified BYOK for the MVP: the key is provided by Husk AI).
-- Saved only after Regolo.ai has accepted it. Never returned by the API, never exported
-- in backups (stripped like sessions), removed with the user.
CREATE TABLE IF NOT EXISTS user_ai_keys (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'regolo',
    api_key TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    model TEXT,
    verified_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_user_ai_keys_org ON user_ai_keys(org_id);

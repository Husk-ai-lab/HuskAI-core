-- Connectors (Telegram, Gmail): how automations send messages, emails and reports.
-- Status only. The credentials (bot token + chat id, Gmail address + app password) live in the OS keychain,
-- never in this table, never in backups.
CREATE TABLE IF NOT EXISTS connectors (
    org_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'not_configured',
    hint TEXT,
    target_hint TEXT,
    verified_at TEXT,
    last_ok_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (org_id, kind)
);

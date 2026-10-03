-- Husk local database. Every product table is scoped by org_id (B2B multi-tenancy).
-- Timestamps are ISO-8601 UTC strings; JSON columns are TEXT.

CREATE TABLE IF NOT EXISTS orgs (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES orgs(id),
    email TEXT,
    display_name TEXT,
    roles TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    last_login_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    department TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    rows INTEGER,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    excerpt TEXT,
    error TEXT,
    authorized_at TEXT NOT NULL,
    uploaded_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    markdown TEXT,
    UNIQUE (org_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_documents_org ON documents(org_id);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading TEXT,
    text TEXT NOT NULL,
    start INTEGER NOT NULL DEFAULT 0,
    "end" INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(document_id);
CREATE INDEX IF NOT EXISTS ix_chunks_org ON chunks(org_id);

CREATE TABLE IF NOT EXISTS tabular_profiles (
    org_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sheet TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (document_id, sheet)
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step TEXT,
    progress REAL NOT NULL DEFAULT 0,
    started_by TEXT NOT NULL,
    document_ids TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    trace_id TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_org ON discovery_runs(org_id, created_at);

CREATE TABLE IF NOT EXISTS discovery_steps (
    run_id TEXT NOT NULL REFERENCES discovery_runs(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    output TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, step)
);

CREATE TABLE IF NOT EXISTS doc_signals (
    org_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, document_id)
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_opps_run ON opportunities(run_id);

CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_facts_org ON facts(org_id);

CREATE TABLE IF NOT EXISTS fact_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT NOT NULL,
    fact_id TEXT NOT NULL,
    replaced_by TEXT,
    reason TEXT,
    data TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS networks (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    automation_id TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_networks_org ON networks(org_id, run_id);

CREATE TABLE IF NOT EXISTS network_notes (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    network_id TEXT NOT NULL REFERENCES networks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notes_network ON network_notes(network_id);

-- Versioned markdown memory (company profile, network summaries, notes bundles).
CREATE TABLE IF NOT EXISTS memory_docs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    version INTEGER NOT NULL,
    markdown TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (org_id, kind, scope, version)
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_automations_org ON automations(org_id);

CREATE TABLE IF NOT EXISTS automation_versions (
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (automation_id, version)
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    automation_id TEXT NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    started_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_autoruns ON automation_runs(automation_id, started_at);

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    automation_id TEXT,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chats ON chats(org_id, automation_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_msgs ON chat_messages(chat_id, created_at);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_checkpoints (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    step TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ckpt ON agent_checkpoints(run_id, agent, step);

CREATE TABLE IF NOT EXISTS token_usage (
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    day TEXT NOT NULL,
    model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (org_id, user_id, day, model)
);

-- Suggestions produced in the background when documents are added or removed.
-- They never change anything by themselves: the user opens them and the Builder proposes.
CREATE TABLE IF NOT EXISTS suggestions (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    automation_id TEXT,
    document_ids TEXT NOT NULL DEFAULT '[]',
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sugg_org ON suggestions(org_id, status, created_at);

-- Relative path inside an uploaded folder (display only).
ALTER TABLE documents ADD COLUMN source_path TEXT;

-- Document cards: the short shared summary of each document that discovery explores first.
-- Reused while the file (sha256) and the card format (version) are unchanged.
CREATE TABLE IF NOT EXISTS document_cards (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    version INTEGER NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cards_org ON document_cards(org_id);

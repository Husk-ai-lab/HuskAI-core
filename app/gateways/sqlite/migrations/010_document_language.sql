-- The language a document is written in (it | en), detected when it is ingested. Summaries follow it.
-- NULL for documents ingested before this migration: detected on the fly when their card is built.
ALTER TABLE documents ADD COLUMN language TEXT;

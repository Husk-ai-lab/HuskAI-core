-- Run errors are stored as message keys + params so the UI can show them in any language.
ALTER TABLE discovery_runs ADD COLUMN error_key TEXT;
ALTER TABLE discovery_runs ADD COLUMN error_params TEXT;

-- Whether a discovery run's trace was exported to a running Husk Studio (the UI then links to it;
-- otherwise it offers Studio's home or the install page).
ALTER TABLE discovery_runs ADD COLUMN traced INTEGER NOT NULL DEFAULT 0;

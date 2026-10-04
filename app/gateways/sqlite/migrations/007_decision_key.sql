-- The decision-model key (Venice AI, for Jev) stored next to the Regolo key: entered together after login,
-- verified separately, deleted on sign-out, never exported.
ALTER TABLE user_ai_keys ADD COLUMN decision_key TEXT;
ALTER TABLE user_ai_keys ADD COLUMN decision_hint TEXT;

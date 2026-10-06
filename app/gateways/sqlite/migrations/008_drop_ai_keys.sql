-- No more bring-your-own AI key: all AI goes through husk-gateway with the user's Clerk token.
-- Dropping the table also removes any key an older build saved on this computer.
DROP TABLE IF EXISTS user_ai_keys;

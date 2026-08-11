-- Backgrounds used to be per-user. They cannot be promoted to a shared value
-- safely, so discard only those old preference rows and recreate chat-level state.
DROP TABLE IF EXISTS chat_background_preferences;
CREATE TABLE chat_background_preferences (
    chat_id VARCHAR PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    background_type VARCHAR(32) NOT NULL DEFAULT 'default',
    background_value TEXT NULL,
    updated_at INTEGER NOT NULL,
    updated_by_user_id VARCHAR NULL REFERENCES users(id) ON DELETE SET NULL
);

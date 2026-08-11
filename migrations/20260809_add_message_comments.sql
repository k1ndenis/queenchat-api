CREATE TABLE IF NOT EXISTS message_comments (
 id VARCHAR PRIMARY KEY, message_id VARCHAR NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 channel_id VARCHAR NOT NULL REFERENCES chats(id) ON DELETE CASCADE, user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 content TEXT NOT NULL, created_at INTEGER NOT NULL, edited_at INTEGER NULL, deleted_at INTEGER NULL
);
CREATE INDEX IF NOT EXISTS ix_message_comments_message_created ON message_comments (message_id, created_at);

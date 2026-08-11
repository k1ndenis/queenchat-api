ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS edited_at INTEGER,
    ADD COLUMN IF NOT EXISTS deleted_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_messages_deleted_at ON messages(deleted_at);
CREATE INDEX IF NOT EXISTS idx_messages_edited_at ON messages(edited_at);

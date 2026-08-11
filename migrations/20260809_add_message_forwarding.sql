ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS forwarded_from_message_id VARCHAR REFERENCES messages(id) ON DELETE SET NULL;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS forwarded_from_user_id VARCHAR REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_messages_forwarded_from_message_id
    ON messages (forwarded_from_message_id);

CREATE INDEX IF NOT EXISTS ix_messages_forwarded_from_user_id
    ON messages (forwarded_from_user_id);

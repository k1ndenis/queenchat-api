CREATE TABLE IF NOT EXISTS message_reactions (
    id VARCHAR PRIMARY KEY,
    message_id VARCHAR NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emoji VARCHAR(16) NOT NULL,
    created_at INTEGER NOT NULL,
    CONSTRAINT uq_message_reactions_message_user UNIQUE (message_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_message_reactions_message_id
    ON message_reactions (message_id);

CREATE INDEX IF NOT EXISTS ix_message_reactions_user_id
    ON message_reactions (user_id);

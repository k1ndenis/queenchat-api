CREATE TABLE IF NOT EXISTS reaction_notifications (
    id VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    chat_id VARCHAR NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    message_id VARCHAR NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    reaction_user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    emoji VARCHAR(16) NOT NULL,
    created_at INTEGER NOT NULL,
    read_at INTEGER NULL,
    CONSTRAINT uq_reaction_notifications_target_message_reactor
        UNIQUE (user_id, message_id, reaction_user_id)
);

CREATE INDEX IF NOT EXISTS ix_reaction_notifications_user_chat_unread
    ON reaction_notifications (user_id, chat_id, read_at);

CREATE INDEX IF NOT EXISTS ix_reaction_notifications_message_reactor
    ON reaction_notifications (message_id, reaction_user_id);

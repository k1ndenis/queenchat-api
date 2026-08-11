# app/repositories/message_repository.py
from sqlalchemy.orm import Session
from sqlalchemy import func
import uuid
import time
from app.core.database import MessageORM, MessageReactionORM, ReactionNotificationORM
from app.models.message import ALLOWED_REACTIONS

class MessageRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_message(self,
        chat_id: str,
        sender_id: str,
        content: str,
        sticker_id: str = None,
        is_sticker: bool = False,
        is_image: bool = False,
        images: str = None,
        media: str = None,
        reply_to_id: str = None,
        forwarded_from_message_id: str = None,
        forwarded_from_user_id: str = None
    ) -> MessageORM:
        message = MessageORM(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            sender_id=sender_id,
            content=content,
            sticker_id=sticker_id,
            is_sticker=is_sticker,
            is_image=is_image,
            images=images,
            media=media,
            reply_to_id=reply_to_id,
            forwarded_from_message_id=forwarded_from_message_id,
            forwarded_from_user_id=forwarded_from_user_id,
            created_at=int(time.time()),
            is_read=False
        )
        print(f"📸 REPO: is_image={is_image}, reply_to_id={reply_to_id}, content={content[:50] if content else None}")
        self.db.add(message)
        self.db.flush()
        return message

    def get_chat_messages(self, chat_id: str, limit: int = None, offset: int = 0) -> list[MessageORM]:
        query = self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).order_by(MessageORM.created_at.asc())
        
        if limit is not None:
            query = query.offset(offset).limit(limit)
        
        messages = query.all()
        print(f"📚 Loaded {len(messages)} messages from chat {chat_id} (limit={limit})")
        return messages

    def mark_as_read(self, message_id: str, user_id: str) -> MessageORM | None:
        message = self.db.query(MessageORM).filter(
            MessageORM.id == message_id,
            MessageORM.sender_id != user_id
        ).first()
        
        if message and not message.is_read:
            message.is_read = True
            self.db.flush()
            print(f"✓ Message {message_id} marked as read by {user_id}")
            return message
        
        return None

    def get_last_message(self, chat_id: str) -> MessageORM | None:
        return self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).order_by(MessageORM.created_at.desc()).first()
    
    def get_unread_count(self, chat_id: str, user_id: str) -> int:
        return self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id,
            MessageORM.sender_id != user_id,
            MessageORM.is_read == False
        ).count()

    def mark_all_as_read(self, chat_id: str, user_id: str) -> int:
        result = self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id,
            MessageORM.sender_id != user_id,
            MessageORM.is_read == False
        ).update({"is_read": True}, synchronize_session=False)
        self.db.flush()
        print(f"📖 [REPO] Marked {result} messages as read in chat {chat_id} for user {user_id}")
        return result

    def delete_by_chat(self, chat_id: str) -> None:
        message_ids = [
            row[0] for row in self.db.query(MessageORM.id).filter(
                MessageORM.chat_id == chat_id
            ).all()
        ]
        if message_ids:
            self.db.query(MessageReactionORM).filter(
                MessageReactionORM.message_id.in_(message_ids)
            ).delete(synchronize_session=False)
        self.db.query(MessageORM).filter(
            MessageORM.chat_id == chat_id
        ).delete(synchronize_session=False)

    def get_message(self, message_id: str) -> MessageORM | None:
        return self.db.query(MessageORM).filter(MessageORM.id == message_id).first()

    def update_message_content(self, message_id: str, content: str, edited_at: int) -> MessageORM | None:
        message = self.get_message(message_id)
        if not message:
            return None
        message.content = content
        message.edited_at = edited_at
        self.db.flush()
        return message

    def soft_delete_message(self, message_id: str, deleted_at: int) -> MessageORM | None:
        message = self.get_message(message_id)
        if not message:
            return None
        message.deleted_at = deleted_at
        self.db.flush()
        return message

    def set_reaction(self, message_id: str, user_id: str, emoji: str) -> MessageReactionORM:
        reaction = self.db.query(MessageReactionORM).filter(
            MessageReactionORM.message_id == message_id,
            MessageReactionORM.user_id == user_id
        ).first()

        if reaction:
            reaction.emoji = emoji
        else:
            reaction = MessageReactionORM(
                id=str(uuid.uuid4()),
                message_id=message_id,
                user_id=user_id,
                emoji=emoji,
                created_at=int(time.time())
            )
            self.db.add(reaction)

        self.db.flush()
        return reaction

    def get_reaction(self, message_id: str, user_id: str) -> MessageReactionORM | None:
        return self.db.query(MessageReactionORM).filter(
            MessageReactionORM.message_id == message_id,
            MessageReactionORM.user_id == user_id,
        ).first()

    def delete_reaction(self, message_id: str, user_id: str) -> bool:
        deleted = self.db.query(MessageReactionORM).filter(
            MessageReactionORM.message_id == message_id,
            MessageReactionORM.user_id == user_id
        ).delete(synchronize_session=False)
        self.db.flush()
        return deleted > 0

    def upsert_reaction_notification(self, *, user_id: str, chat_id: str, message_id: str, reaction_user_id: str, emoji: str) -> ReactionNotificationORM:
        notification = self.db.query(ReactionNotificationORM).filter(
            ReactionNotificationORM.user_id == user_id,
            ReactionNotificationORM.message_id == message_id,
            ReactionNotificationORM.reaction_user_id == reaction_user_id,
        ).first()
        if notification:
            notification.emoji = emoji
            notification.created_at = int(time.time())
            notification.read_at = None
        else:
            notification = ReactionNotificationORM(
                id=str(uuid.uuid4()), user_id=user_id, chat_id=chat_id, message_id=message_id,
                reaction_user_id=reaction_user_id, emoji=emoji, created_at=int(time.time()), read_at=None,
            )
            self.db.add(notification)
        self.db.flush()
        return notification

    def remove_reaction_notification(self, *, user_id: str, message_id: str, reaction_user_id: str) -> int:
        deleted = self.db.query(ReactionNotificationORM).filter(
            ReactionNotificationORM.user_id == user_id,
            ReactionNotificationORM.message_id == message_id,
            ReactionNotificationORM.reaction_user_id == reaction_user_id,
        ).delete(synchronize_session=False)
        self.db.flush()
        return deleted

    def remove_reaction_notifications_for_message(self, message_id: str) -> int:
        deleted = self.db.query(ReactionNotificationORM).filter(
            ReactionNotificationORM.message_id == message_id,
        ).delete(synchronize_session=False)
        self.db.flush()
        return deleted

    def mark_reaction_notifications_read(self, chat_id: str, user_id: str) -> int:
        result = self.db.query(ReactionNotificationORM).filter(
            ReactionNotificationORM.chat_id == chat_id,
            ReactionNotificationORM.user_id == user_id,
            ReactionNotificationORM.read_at.is_(None),
        ).update({"read_at": int(time.time())}, synchronize_session=False)
        self.db.flush()
        return result

    def get_reaction_summaries(self, message_ids: list[str], user_id: str) -> dict[str, list[dict]]:
        if not message_ids:
            return {}

        counts = self.db.query(
            MessageReactionORM.message_id,
            MessageReactionORM.emoji,
            func.count(MessageReactionORM.id)
        ).filter(
            MessageReactionORM.message_id.in_(message_ids)
        ).group_by(
            MessageReactionORM.message_id,
            MessageReactionORM.emoji
        ).all()

        my_reactions = self.db.query(
            MessageReactionORM.message_id,
            MessageReactionORM.emoji
        ).filter(
            MessageReactionORM.message_id.in_(message_ids),
            MessageReactionORM.user_id == user_id
        ).all()

        reacted_by_me = {(message_id, emoji) for message_id, emoji in my_reactions}
        grouped: dict[str, dict[str, int]] = {}
        for message_id, emoji, count in counts:
            grouped.setdefault(message_id, {})[emoji] = int(count)

        summaries: dict[str, list[dict]] = {}
        for message_id, emoji_counts in grouped.items():
            summaries[message_id] = [
                {
                    "emoji": emoji,
                    "count": emoji_counts[emoji],
                    "reacted_by_me": (message_id, emoji) in reacted_by_me,
                }
                for emoji in ALLOWED_REACTIONS
                if emoji in emoji_counts
            ]

        return summaries

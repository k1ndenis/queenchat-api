from sqlalchemy.orm import Session
import time
from app.repositories.message_repository import MessageRepository
from app.core.redis import redis_cache
from app.core.database import MessageORM

class MessageService:
    def __init__(self, db: Session):
        self.repo = MessageRepository(db)
    
    def create_message(self,
        chat_id: str,
        sender_id: str,
        content: str = None,
        sticker_id: str = None,
        is_image: bool = False,
        images: str = None,
        media: str = None,
        reply_to_id: str = None,
        forwarded_from_message_id: str = None,
        forwarded_from_user_id: str = None
    ):
        is_sticker = sticker_id is not None
        message = self.repo.create_message(
            chat_id,
            sender_id,
            content,
            sticker_id,
            is_sticker,
            is_image,
            images, media,
            reply_to_id,
            forwarded_from_message_id,
            forwarded_from_user_id
        )
        redis_cache.delete(f"chat_messages:{chat_id}")
        return message
    
    def get_chat_messages(self, chat_id: str, limit: int = None, offset: int = 0) -> list[MessageORM]:
        return self.repo.get_chat_messages(chat_id, limit, offset)

    def mark_as_read(self, message_id: str, user_id: str) -> MessageORM | None:
        return self.repo.mark_as_read(message_id, user_id)

    def get_last_message(self, chat_id: str) -> MessageORM | None:
        return self.repo.get_last_message(chat_id)
    
    def get_unread_count(self, chat_id: str, user_id: str) -> int:
        return self.repo.get_unread_count(chat_id, user_id)

    def mark_all_as_read(self, chat_id: str, user_id: str) -> int:
        return self.repo.mark_all_as_read(chat_id, user_id)

    def get_message(self, message_id: str) -> MessageORM | None:
        return self.repo.get_message(message_id)

    def update_message_content(self, message_id: str, chat_id: str, content: str) -> MessageORM | None:
        message = self.repo.update_message_content(message_id, content, int(time.time()))
        redis_cache.delete(f"chat_messages:{chat_id}")
        return message

    def soft_delete_message(self, message_id: str, chat_id: str) -> MessageORM | None:
        message = self.repo.soft_delete_message(message_id, int(time.time()))
        redis_cache.delete(f"chat_messages:{chat_id}")
        return message

    def set_reaction(self, message_id: str, user_id: str, emoji: str):
        return self.repo.set_reaction(message_id, user_id, emoji)

    def get_reaction(self, message_id: str, user_id: str):
        return self.repo.get_reaction(message_id, user_id)

    def delete_reaction(self, message_id: str, user_id: str) -> bool:
        return self.repo.delete_reaction(message_id, user_id)

    def upsert_reaction_notification(self, **kwargs):
        return self.repo.upsert_reaction_notification(**kwargs)

    def remove_reaction_notification(self, **kwargs) -> int:
        return self.repo.remove_reaction_notification(**kwargs)

    def remove_reaction_notifications_for_message(self, message_id: str) -> int:
        return self.repo.remove_reaction_notifications_for_message(message_id)

    def mark_reaction_notifications_read(self, chat_id: str, user_id: str) -> int:
        return self.repo.mark_reaction_notifications_read(chat_id, user_id)

    def get_reaction_summaries(self, message_ids: list[str], user_id: str) -> dict[str, list[dict]]:
        return self.repo.get_reaction_summaries(message_ids, user_id)

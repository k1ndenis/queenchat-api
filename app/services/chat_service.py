from sqlalchemy.orm import Session
from app.repositories.chat_repository import ChatRepository
from app.models.chat import ChatResponse, ParticipantResponse
import time
from app.repositories.auth_repository import AuthRepository
from app.core.redis import redis_cache

class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ChatRepository(db)
        self.auth_repo = AuthRepository(db)

    def get_user_chats(self, user_id: str):
        cache_key = f"user_chats:{user_id}"
        cached = redis_cache.get(cache_key)
        if cached:
            return [ChatResponse(**chat) for chat in cached]
        
        chats = self.repo.get_user_chats(user_id)
        result = []
        for chat in chats:
            participants = []
            for participant in chat.participants:
                participants.append(ParticipantResponse(
                    user_id=participant.id,
                    username=participant.username,
                    avatar=participant.avatar if hasattr(participant, 'avatar') else None,
                    joined_at=chat.created_at
                ))
            result.append(ChatResponse(
                id=chat.id,
                name=chat.name,
                is_group=chat.is_group,
                created_by=chat.created_by,
                created_at=chat.created_at,
                updated_at=chat.updated_at,
                participants=participants
            ))
        
        redis_cache.set(cache_key, [chat.model_dump() for chat in result])
        return result

    def get_chat(self, chat_id: str) -> ChatResponse | None:
        cache_key = f"chat:{chat_id}"
        cached = redis_cache.get(cache_key)
        if cached:
            return ChatResponse(**cached)
        
        chat = self.repo.get_chat(chat_id)
        if not chat:
            return None
        
        participants = []
        for user in chat.participants:
            participants.append({
                "user_id": user.id,
                "username": user.username,
                "joined_at": chat.created_at
            })
        
        result = ChatResponse(
            id=chat.id,
            name=chat.name,
            is_group=chat.is_group,
            created_by=chat.created_by,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            participants=participants
        )
        
        redis_cache.set(cache_key, result.model_dump())
        return result

    def create_chat(self, name: str, is_group: bool, created_by: str, participant_ids: list[str]):
        chat = self.repo.create_chat(name, is_group, created_by)
        
        all_participants = list(set(participant_ids + [created_by]))
        for user_id in all_participants:
            self.repo.add_participant(chat.id, user_id)
        
        for user_id in all_participants:
            redis_cache.delete(f"user_chats:{user_id}")
        
        participants = []
        for user_id in all_participants:
            user = self.auth_repo.get_by_id(user_id)
            participants.append({
                "user_id": user_id,
                "username": user.username,
                "joined_at": int(time.time())
            })
        
        return ChatResponse(
            id=chat.id,
            name=chat.name,
            is_group=chat.is_group,
            created_by=chat.created_by,
            created_at=chat.created_at,
            updated_at=chat.updated_at,
            participants=participants
        )

    def delete_chat(self, chat_id: str):
        chat = self.repo.get_chat(chat_id)
        if chat:
            for participant in chat.participants:
                redis_cache.delete(f"user_chats:{participant.id}")
        
        redis_cache.delete(f"chat:{chat_id}")
        
        import traceback
        try:
            result = self.repo.delete_chat(chat_id)
            if not result:
                print(f"ОШИБКА: Не удалось удалить чат {chat_id}")
            return result
        except Exception as e:
            print(f"КРИТИЧЕСКАЯ ОШИБКА при удалении: {e}")
            print(traceback.format_exc())
            raise

    def is_participant(self, chat_id: str, user_id: str) -> bool:
        return self.repo.is_participant(chat_id, user_id)

    def add_participant(self, chat_id: str, user_id: str):
        result = self.repo.add_participant(chat_id, user_id)
        redis_cache.delete(f"user_chats:{user_id}")
        redis_cache.delete(f"chat:{chat_id}")
        return result

    def remove_participant(self, chat_id: str, user_id: str):
        result = self.repo.remove_participant(chat_id, user_id)
        redis_cache.delete(f"user_chats:{user_id}")
        redis_cache.delete(f"chat:{chat_id}")
        return result
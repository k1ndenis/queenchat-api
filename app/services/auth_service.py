from sqlalchemy.orm import Session
from fastapi import HTTPException
import random
import os

from app.repositories.auth_repository import AuthRepository
from app.repositories.chat_repository import ChatRepository
from app.repositories.message_repository import MessageRepository
from app.models.user import UserSchema
from app.models.auth import RegisterRequest, LoginRequest
from app.core.security import hash_password, create_token, verify_password
from app.core.redis import redis_cache
from app.core.database import UserORM
from app.services.sms_service import SMSService


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = AuthRepository(db)
        self.chat_repo = ChatRepository(db)
        self.message_repo = MessageRepository(db)
        self.sms_service = SMSService(api_id=os.getenv("SMS_API_ID", ""))

    def get_user_profile(self, user_id: str) -> UserSchema | None:
        cached = redis_cache.get(f"user_profile:{user_id}")
        if cached:
            return UserSchema(**cached)
        
        user_orm = self.repository.get_by_id(user_id)
        if not user_orm:
            return None
        
        result = UserSchema(
            id=user_orm.id,
            username=user_orm.username,
            phone=user_orm.phone,
            avatar=user_orm.avatar,
            created_at=user_orm.created_at
        )
        
        redis_cache.set(f"user_profile:{user_id}", result.model_dump(), ttl=300)
        
        return result

    def get_all_users(self, exclude_current: bool = True, current_user_id: str = None) -> list[UserSchema]:
        cache_key = "all_users"
        if exclude_current and current_user_id:
            cache_key = f"all_users_exclude_{current_user_id}"
        
        cached = redis_cache.get(cache_key)
        if cached:
            return [UserSchema(**user) for user in cached]
        
        users_orm = self.repository.get_all_users(
            exclude_user_id=current_user_id if exclude_current else None
        )
        
        result = [
            UserSchema(
                id=user_orm.id,
                username=user_orm.username,
                phone=user_orm.phone,
                avatar=user_orm.avatar,
                created_at=user_orm.created_at
            ) for user_orm in users_orm
        ]
        
        redis_cache.set(cache_key, [user.model_dump() for user in result], ttl=300)
        
        return result

    def send_verification_code(self, phone: str):
        existing = self.repository.get_by_phone(phone)
        if existing:
            raise HTTPException(status_code=400, detail="Phone already registered")
        
        code = f"{random.randint(100000, 999999)}"
        
        redis_cache.setex(f"sms_verify:{phone}", 300, code)
        
        print(f"\n📱 [TEST] Код для {phone}: {code}\n")
        
        return {"status": "ok", "message": "Код отправлен"}

    def send_reset_code(self, phone: str):
        """Отправить код для восстановления пароля"""
        user = self.repository.get_by_phone(phone)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь с таким номером не найден")
        
        code = f"{random.randint(100000, 999999)}"
        redis_cache.setex(f"reset_code:{phone}", 300, code)
        
        print(f"\n🔐 [RESET PASSWORD] Код для {phone}: {code}\n")
        
        return {"status": "ok", "message": "Код отправлен"}


    def verify_reset_code(self, phone: str, code: str):
        from app.core.redis import redis_client
        
        saved_code = redis_client.get(f"reset_code:{phone}")
        if saved_code:
            saved_code = saved_code.decode() if isinstance(saved_code, bytes) else saved_code
        
        if not saved_code or saved_code != code:
            raise HTTPException(400, "Неверный код")
        
        return {"status": "ok", "message": "Код подтвержден"}

    def reset_password(self, phone: str, code: str, new_password: str):
        """Сбросить пароль по коду"""
        from app.core.redis import redis_client
        
        saved_code = redis_client.get(f"reset_code:{phone}")
        if saved_code:
            saved_code = saved_code.decode() if isinstance(saved_code, bytes) else saved_code
        
        if not saved_code or saved_code != code:
            raise HTTPException(400, "Неверный код подтверждения")
        
        user = self.repository.get_by_phone(phone)
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        
        hashed_password = hash_password(new_password)
        user.password_hash = hashed_password
        self.db.commit()
        
        redis_client.delete(f"reset_code:{phone}")
        
        return {"status": "ok", "message": "Пароль успешно изменен"}

    def register(self, phone: str, username: str, password: str, code: str):
        saved_code = redis_cache.get_raw(f"sms_verify:{phone}")
        
        if isinstance(saved_code, bytes):
            saved_code = saved_code.decode()
        elif isinstance(saved_code, dict):
            saved_code = saved_code.get("value") or saved_code.get("data")
        
        if not saved_code or saved_code != code:
            raise HTTPException(400, "Неверный код подтверждения")
        
        if self.repository.get_by_phone(phone):
            raise HTTPException(status_code=400, detail="Phone already registered")
        
        if self.repository.get_by_username(username):
            raise HTTPException(status_code=400, detail="Username already taken")
        
        hashed_password = hash_password(password)
        user = self.repository.create_user(
            username=username,
            phone=phone,
            password_hash=hashed_password
        )
        
        redis_cache.delete(f"sms_verify:{phone}")
        redis_cache.delete("all_users")
        redis_cache.delete_pattern("all_users_exclude_*")
        
        token = create_token(user.id, user.username)
        
        return {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "phone": user.phone,
                "avatar": user.avatar
            }
        }
    
    def login(self, phone: str, password: str):
        user = self.repository.get_by_phone(phone)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid phone or password")
        
        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid phone or password")
        
        token = create_token(user.id, user.username)
        
        return {
            "token": token,
            "user": {
                "id": user.id,
                "username": user.username,
                "phone": user.phone,
                "avatar": user.avatar
            }
        }
    
    def delete_user(self, user_id: str) -> bool:
        try:
            user_chats = self.chat_repo.get_user_chats(user_id)
            
            for chat in user_chats:
                self.message_repo.delete_by_chat(chat.id)
                self.chat_repo.delete_participants(chat.id)
                self.chat_repo.delete_chat(chat.id)
            
            redis_cache.delete("all_users")
            redis_cache.delete_pattern("all_users_exclude_*")
            redis_cache.delete(f"user_profile:{user_id}")
            redis_cache.delete(f"user:{user_id}")
            redis_cache.delete(f"user_chats:{user_id}")
            
            result = self.repository.delete_user(user_id)
            
            self.db.commit()
            return result
            
        except Exception as e:
            self.db.rollback()
            print(f"Error deleting user {user_id}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def update_profile(self, user_id: str, username: str, phone: str = None, avatar: str = None) -> UserORM | None:
        user = self.repository.update_user(user_id, username, phone, avatar)
        
        if user:
            redis_cache.delete(f"user_profile:{user_id}")
            redis_cache.delete("all_users")
            redis_cache.delete_pattern("all_users_exclude_*")
        
        return user
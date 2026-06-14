from sqlalchemy.orm import Session
from uuid import uuid4
import time

from app.core.database import UserORM

class AuthRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
    
    def get_by_id(self, user_id: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.id == user_id).first()

    def get_by_username(self, username: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.username == username).first()

    def get_by_phone(self, phone: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.phone == phone).first()

    def get_all_users(self, exclude_user_id: str = None) -> list[UserORM]:
        query = self.db.query(UserORM)
        if exclude_user_id:
            query = query.filter(UserORM.id != exclude_user_id)
        return query.all()

    def create_user(self, username: str, phone: str, password_hash: str) -> UserORM:
        new_user = UserORM(
            id=str(uuid4()),
            username=username,
            phone=phone,
            password_hash=password_hash,
            created_at=int(time.time())
        )
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user

    def update_user(self, user_id: str, username: str = None, phone: str = None, avatar: str = None) -> UserORM | None:
        user = self.get_by_id(user_id)
        if user:
            if username is not None:
                user.username = username
            if phone is not None:
                user.phone = phone
            if avatar is not None:
                user.avatar = avatar
            self.db.flush()
        return user

    def delete_user(self, user_id: str) -> bool:
        try:
            user = self.get_by_id(user_id)
            if user:
                self.db.delete(user)
                self.db.flush()
                return True
            return False
        except Exception as e:
            print(f"Error deleting user: {e}")
            self.db.rollback()
            return False
    
    def update_password(self, user_id: str, new_password_hash: str) -> bool:
        user = self.get_by_id(user_id)
        if user:
            user.password_hash = new_password_hash
            self.db.flush()
            return True
        return False
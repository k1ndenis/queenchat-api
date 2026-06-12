from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import uuid4
import time

from app.models.user import UserSchema
from app.core.database import UserORM

class AuthRepository:
    def __init__(self, db: Session) -> None:
        self.db = db
    
    def get_by_id(self, user_id: str) -> UserORM | None:  # Уже есть
        return self.db.query(UserORM).filter(UserORM.id == user_id).first()

    def get_by_username(self, username: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.username == username).first()

    def get_by_email(self, email: str) -> UserORM | None:
        return self.db.query(UserORM).filter(UserORM.email == email).first()

    def get_all_users(self, exclude_user_id: str = None) -> list[UserORM]:
        query = self.db.query(UserORM)
        if exclude_user_id:
            query = query.filter(UserORM.id != exclude_user_id)
        return query.all()

    def create_user(self, username: str, email: str, password_hash: str) -> UserORM:
        new_user = UserORM(
            id=str(uuid4()),
            username=username,
            email=email,
            password_hash=password_hash,
            created_at=int(time.time())
        )
        self.db.add(new_user)
        self.db.commit()
        self.db.refresh(new_user)
        return new_user

    def update_user(self, user_id: str, username: str = None, email: str = None, avatar: str = None) -> UserORM | None:
        user = self.get_by_id(user_id)
        if user:
            if username is not None:
                user.username = username
            if email is not None:
                user.email = email
            if avatar is not None:
                user.avatar = avatar
            self.db.commit()
            self.db.refresh(user)
        return user

    def delete_user(self, user_id: str) -> bool:
        try:
            user = self.db.query(UserORM).filter(UserORM.id == user_id).first()
            if user:
                self.db.delete(user)
                self.db.commit()
                return True
            return False
        except Exception as e:
            print(f"Error deleting user: {e}")
            self.db.rollback()
            return False
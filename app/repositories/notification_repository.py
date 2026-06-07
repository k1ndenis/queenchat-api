from sqlalchemy.orm import Session
from app.core.database import NotificationORM
import uuid
import time

class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, user_id: str, chat_id: str, title: str, message: str, type: str):
        notification = NotificationORM(
            id=str(uuid.uuid4()),
            user_id=user_id,
            chat_id=chat_id,
            title=title,
            message=message,
            type=type,
            is_read=False,
            created_at=int(time.time())
        )
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        return notification
    
    def get_by_user(self, user_id: str, limit: int, offset: int):
        return self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id
        ).order_by(
            NotificationORM.created_at.desc()
        ).offset(offset).limit(limit).all()
    
    def get_unread_count(self, user_id: str):
        return self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id,
            NotificationORM.is_read == False
        ).count()
    
    def mark_as_read(self, notification_id: str, user_id: str):
        notification = self.db.query(NotificationORM).filter(
            NotificationORM.id == notification_id,
            NotificationORM.user_id == user_id
        ).first()
        if notification and not notification.is_read:
            notification.is_read = True
            self.db.commit()
            return True
        return False
    
    def mark_all_as_read(self, user_id: str):
        self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id,
            NotificationORM.is_read == False
        ).update({"is_read": True})
        self.db.commit()

    def delete_old_notifications(self, user_id: str, days: int = 30):
        cutoff_time = int(time.time()) - (days * 86400)
        deleted = self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id,
            NotificationORM.created_at < cutoff_time
        ).delete()
        self.db.commit()
        return deleted

    def delete_all_read(self, user_id: str):
        deleted = self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id,
            NotificationORM.is_read == True
        ).delete()
        self.db.commit()
        return deleted

    def limit_notifications(self, user_id: str, max_count: int = 100):
        notifications = self.db.query(NotificationORM.id).filter(
            NotificationORM.user_id == user_id
        ).order_by(
            NotificationORM.created_at.desc()
        ).offset(max_count).all()
        
        ids_to_delete = [n[0] for n in notifications]
        if ids_to_delete:
            self.db.query(NotificationORM).filter(
                NotificationORM.id.in_(ids_to_delete)
            ).delete(synchronize_session=False)
            self.db.commit()

    def get_by_user_and_chat(self, user_id: str, chat_id: str, limit: int, offset: int):
        return self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id,
            NotificationORM.chat_id == chat_id
        ).order_by(
            NotificationORM.created_at.desc()
        ).offset(offset).limit(limit).all()

    def mark_by_chat_as_read(self, user_id: str, chat_id: str) -> int:
        result = self.db.query(NotificationORM).filter(
            NotificationORM.user_id == user_id,
            NotificationORM.chat_id == chat_id,
            NotificationORM.is_read == False
        ).update({"is_read": True}, synchronize_session=False)
        self.db.flush()
        print(f"📖 [REPO] Marked {result} notifications as read for chat {chat_id}")
        return result

    def delete_by_chat(self, chat_id: str) -> None:
        self.db.query(NotificationORM).filter(
            NotificationORM.chat_id == chat_id
        ).delete(synchronize_session=False)
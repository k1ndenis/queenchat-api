from sqlalchemy.orm import Session
from app.repositories.notification_repository import NotificationRepository
from app.core.websocket import manager
import asyncio
import os

class NotificationService:
    def __init__(self, db: Session):
        self.repo = NotificationRepository(db)
    
    def create_notification(self, user_id: str, chat_id: str, title: str, message: str, type: str = "info"):
        print(f"🔔 [SERVICE] Creating notification: user_id={user_id}, chat_id={chat_id}, title={title}")
        
        if not chat_id:
            print(f"⚠️ [SERVICE] WARNING: chat_id is None or empty!")
        
        notification = self.repo.create(user_id, chat_id, title, message, type)
        
        print(f"🔔 [SERVICE] Notification created with id={notification.id}, chat_id={notification.chat_id}")
        
        testing = os.getenv("TESTING", "false").lower() == "true"
        if testing:
            print(f"🔔 [TEST] Skipping WebSocket notification")
            return notification
        
        asyncio.create_task(
            manager.send_notification(user_id, {
                "id": notification.id,
                "title": title,
                "message": message,
                "type": type,
                "chat_id": chat_id,
                "created_at": notification.created_at,
                "is_read": notification.is_read
            })
        )
        
        return notification
    
    def get_user_notifications(self, user_id: str, limit: int, offset: int):
        return self.repo.get_by_user(user_id, limit, offset)
    
    def get_unread_count(self, user_id: str):
        return self.repo.get_unread_count(user_id)
    
    def mark_as_read(self, notification_id: str, user_id: str):
        return self.repo.mark_as_read(notification_id, user_id)
    
    def mark_all_as_read(self, user_id: str):
        return self.repo.mark_all_as_read(user_id)

    def delete_old_notifications(self, user_id: str, days: int) -> int:
        return self.repo.delete_old_notifications(user_id, days)

    def delete_all_read(self, user_id: str) -> int:
        return self.repo.delete_all_read(user_id)
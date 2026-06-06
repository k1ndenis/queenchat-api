from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.notification_service import NotificationService
from app.models.notification import NotificationResponse, NotificationCreate

router = APIRouter()

@router.get("/", response_model=List[NotificationResponse])
def get_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    chat_id: Optional[str] = None
):
    service = NotificationService(db)
    if chat_id:
        notifications = service.repo.get_by_user_and_chat(current_user.id, chat_id, limit, offset)
        return notifications
    return service.get_user_notifications(current_user.id, limit, offset)

@router.get("/unread/count")
def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = NotificationService(db)
    return {"count": service.get_unread_count(current_user.id)}

@router.patch("/{notification_id}/read")
def mark_as_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = NotificationService(db)
    result = service.mark_as_read(notification_id, current_user.id)
    if not result:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "ok"}

@router.patch("/read/all")
def mark_all_as_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = NotificationService(db)
    service.mark_all_as_read(current_user.id)
    return {"status": "ok"}

@router.patch("/read/by-chat/{chat_id}")
def mark_notifications_by_chat_as_read(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = NotificationService(db)
    count = service.repo.mark_by_chat_as_read(current_user.id, chat_id)
    db.commit()
    return {"status": "ok", "marked_count": count}

@router.delete("/clean/old")
def clean_old_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    days: int = 30
):
    service = NotificationService(db)
    deleted = service.repo.delete_old_notifications(current_user.id, days)
    return {"deleted": deleted}

@router.delete("/clean/read")
def clean_read_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = NotificationService(db)
    deleted = service.repo.delete_all_read(current_user.id)
    return {"deleted": deleted}

@router.post("/clean/limit")
def limit_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    max_count: int = 100
):
    service = NotificationService(db)
    service.repo.limit_notifications(current_user.id, max_count)
    return {"status": "ok"}
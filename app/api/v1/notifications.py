from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from pydantic import BaseModel
import os
import redis
import firebase_admin
from firebase_admin import credentials, messaging

from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.notification_service import NotificationService
from app.models.notification import NotificationResponse

router = APIRouter()

TESTING = os.getenv("TESTING", "false").lower() == "true"

if TESTING:
    REDIS_AVAILABLE = False
    redis_client = None
    print("⚠️ Redis disabled in test mode")
else:
    REDIS_URL = os.getenv("REDIS_URL")
    try:
        redis_client = redis.from_url(REDIS_URL)
        REDIS_AVAILABLE = True
        print("✅ Redis connected for FCM tokens")
    except Exception as e:
        REDIS_AVAILABLE = False
        redis_client = None
        print(f"⚠️ Redis not available: {e}")

try:
    redis_client = redis.from_url(REDIS_URL)
    REDIS_AVAILABLE = True
    print("✅ Redis connected for FCM tokens")
except Exception as e:
    REDIS_AVAILABLE = False
    redis_client = None
    print(f"⚠️ Redis not available: {e}")

fcm_tokens: Dict[str, str] = {}

def save_fcm_token(user_id: str, token: str):
    if REDIS_AVAILABLE:
        redis_client.setex(f"fcm:{user_id}", 60 * 60 * 24 * 30, token)
    else:
        fcm_tokens[user_id] = token

def get_fcm_token(user_id: str) -> str | None:
    if REDIS_AVAILABLE:
        token = redis_client.get(f"fcm:{user_id}")
        return token.decode() if token else None
    return fcm_tokens.get(user_id)

def delete_fcm_token(user_id: str):
    if REDIS_AVAILABLE:
        redis_client.delete(f"fcm:{user_id}")
    else:
        fcm_tokens.pop(user_id, None)

class FCMToken(BaseModel):
    token: str


def get_fcm_token(user_id: str) -> str | None:
    if TESTING:
        return None
    if REDIS_AVAILABLE:
        token = redis_client.get(f"fcm:{user_id}")
        return token.decode() if token else None
    return fcm_tokens.get(user_id)


def get_fcm_token(user_id: str) -> str | None:
    if REDIS_AVAILABLE:
        token = redis_client.get(f"fcm:{user_id}")
        return token.decode() if token else None
    return fcm_tokens.get(user_id)


def delete_fcm_token(user_id: str):
    if REDIS_AVAILABLE:
        redis_client.delete(f"fcm:{user_id}")
    else:
        fcm_tokens.pop(user_id, None)


@router.post("/fcm-token")
def save_token(
    token_data: FCMToken,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    print(f"📝 Saving FCM token for user {current_user.id}")
    save_fcm_token(str(current_user.id), token_data.token)
    return {"status": "ok"}


@router.delete("/fcm-token")
def remove_token(
    current_user: User = Depends(get_current_user),
):
    print(f"🗑️ Removing FCM token for user {current_user.id}")
    delete_fcm_token(str(current_user.id))
    return {"status": "ok"}


@router.get("/fcm-status")
def fcm_status(current_user: User = Depends(get_current_user)):
    token = get_fcm_token(str(current_user.id))
    return {
        "subscribed": token is not None,
        "fcm_available": True,
        "token_preview": token[:20] + "..." if token else None
    }


async def send_fcm_notification(
    user_id: str,
    title: str,
    body: str,
    url: str = "/chat"
):
    token = get_fcm_token(str(user_id))
    
    if not token:
        print(f"⚠️ No FCM token for user {user_id}")
        return False
    
    try:
        message = messaging.Message(
            token=token,
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={
                "title": title,
                "body": body,
                "url": url,
            },
            webpush=messaging.WebpushConfig(
                notification=messaging.WebpushNotification(
                    icon="/favicon.ico",
                    badge="/favicon-96x96.png"
                )
            ),
        )

        response = messaging.send(message)
        print(f"✅ FCM sent to user {user_id}: {response}")
        return True

    except Exception as e:
        print(f"❌ FCM error for user {user_id}: {e}")
        return False


@router.get("/")
def get_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
    chat_id: Optional[str] = None,
):
    service = NotificationService(db)

    if chat_id:
        return service.repo.get_by_user_and_chat(
            current_user.id, chat_id, limit, offset
        )

    return service.get_user_notifications(
        current_user.id, limit, offset
    )


@router.get("/unread/count")
def unread_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = NotificationService(db)
    return {"count": service.get_unread_count(current_user.id)}


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = NotificationService(db)

    ok = service.mark_as_read(notification_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")

    return {"status": "ok"}


@router.patch("/read/all")
def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = NotificationService(db)
    service.mark_all_as_read(current_user.id)
    return {"status": "ok"}

@router.delete("/clean/old")
def clean_old_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    days: int = 30
):
    service = NotificationService(db)
    deleted = service.repo.delete_old_notifications(current_user.id, days)
    db.commit()
    return {"deleted": deleted}


@router.delete("/clean/read")
def clean_read_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    service = NotificationService(db)
    deleted = service.repo.delete_all_read(current_user.id)
    db.commit()
    return {"deleted": deleted}
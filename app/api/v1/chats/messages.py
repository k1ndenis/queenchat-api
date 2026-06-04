from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.core.database import MessageORM
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.services.notification_service import NotificationService
from app.models.message import MessageCreate, MessageResponse
from app.core.websocket import manager
from .utils import validate_chat_id

router = APIRouter()

@router.post("/{chat_id}/messages", response_model=MessageResponse)
async def send_message(
    chat_id: str,
    message_data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    chat_id = validate_chat_id(chat_id)
    
    print(f"🔵 [MESSAGE] Sending to chat {chat_id} from user {current_user.id}")
    
    try:
        message_service = MessageService(db)
        chat_service = ChatService(db)
        
        if not chat_service.is_participant(chat_id, current_user.id):
            print(f"⚠️ User {current_user.id} not in participants, adding...")
            chat_service.add_participant(chat_id, current_user.id)
            db.flush()
        
        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content
        )
        
        db.commit()
        db.refresh(message)
        
        print(f"✅ Message {message.id} COMMITTED to DB at {message.created_at}")
        
        check = db.query(MessageORM).filter(MessageORM.id == message.id).first()
        print(f"   Verification - message in DB: {check is not None}")
        
        try:
            notification_service = NotificationService(db)
            chat = chat_service.get_chat(chat_id)
            
            if chat:
                for participant in chat.participants:
                    if participant.user_id != current_user.id:
                        notification_service.create_notification(
                            user_id=participant.user_id,
                            chat_id=chat_id,
                            title="Новое сообщение",
                            message=f"{current_user.username}: {message.content[:50]}",
                            type="message"
                        )
                        print(f"📨 Notification sent to {participant.user_id}")
        except Exception as notif_error:
            print(f"⚠️ Notification error (non-critical): {notif_error}")
        
        await manager.broadcast_to_chat(
            {
                "type": "new_message",
                "message": {
                    "id": message.id,
                    "sender_id": message.sender_id,
                    "sender_name": current_user.username,
                    "content": message.content,
                    "created_at": message.created_at,
                    "chat_id": chat_id,
                    "is_sticker": getattr(message, 'is_sticker', False),
                    "is_read": message.is_read
                }
            },
            chat_id=chat_id,
            exclude_user_id=current_user.id
        )
        
        return message
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error sending message: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{chat_id}/messages", response_model=List[MessageResponse])
def get_messages(
    chat_id: str,
    limit: int = None,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[MessageResponse]:
    chat_id = validate_chat_id(chat_id)
    
    print(f"🔵 [GET] Loading messages for chat {chat_id}, user {current_user.id}")
    
    try:
        message_service = MessageService(db)
        chat_service = ChatService(db)
        
        chat = chat_service.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        
        if not chat_service.is_participant(chat_id, current_user.id):
            print(f"⚠️ User not in participants, adding...")
            chat_service.add_participant(chat_id, current_user.id)
            db.commit()
        
        messages = message_service.get_chat_messages(chat_id, limit=limit, offset=offset)
        
        print(f"📨 Loaded {len(messages)} messages for user {current_user.id}")
        
        return messages
        
    except Exception as e:
        print(f"❌ Error loading messages: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{chat_id}/messages/{message_id}/read")
def mark_message_as_read(
    chat_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    try:
        service = MessageService(db)
        message = service.mark_as_read(message_id, current_user.id)
        
        if message:
            db.commit()
            
            try:
                import asyncio
                loop = asyncio.get_running_loop()
                loop.create_task(
                    manager.broadcast_to_chat(
                        {
                            "type": "message_read",
                            "message_id": message_id,
                            "user_id": current_user.id,
                            "chat_id": chat_id
                        },
                        chat_id=chat_id,
                        exclude_user_id=None
                    )
                )
            except RuntimeError:
                import asyncio
                asyncio.run(
                    manager.broadcast_to_chat(
                        {
                            "type": "message_read",
                            "message_id": message_id,
                            "user_id": current_user.id,
                            "chat_id": chat_id
                        },
                        chat_id=chat_id,
                        exclude_user_id=None
                    )
                )
            
            return {"status": "ok", "message_id": message_id, "is_read": True}
        else:
            return {"status": "not_found", "message": "Message not found or already read"}
            
    except Exception as e:
        db.rollback()
        print(f"❌ Error marking message as read: {e}")
        raise HTTPException(status_code=500, detail=str(e))
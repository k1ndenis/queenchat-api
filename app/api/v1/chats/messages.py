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
from app.api.v1.chats import validate_chat_id

router = APIRouter()

@router.post("/{chat_id}/messages", response_model=MessageResponse)
async def send_message(
    chat_id: str,
    message_data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> MessageResponse:
    chat_id = validate_chat_id(chat_id)
    
    print(f"📸 RAW message_data: {message_data}")
    print(f"📸 message_data.is_image: {message_data.is_image}")
    
    try:
        message_service = MessageService(db)
        chat_service = ChatService(db)
        
        if not chat_service.is_participant(chat_id, current_user.id):
            print(f"⚠️ User {current_user.id} not in participants, adding...")
            chat_service.add_participant(chat_id, current_user.id)
            db.flush()
        
        print(f"📸 BEFORE CREATE: is_image={message_data.is_image}, content={message_data.content[:50] if message_data.content else None}")

        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content,
            is_image=message_data.is_image
)
        
        db.commit()
        db.refresh(message)
        
        print(f"✅ Message {message.id} COMMITTED to DB")
        
        print("=" * 60)
        print("🔔 [NOTIFICATION] Starting notification creation")
        print(f"🔔 [NOTIFICATION] Chat ID: {chat_id}")
        print(f"🔔 [NOTIFICATION] Current user: {current_user.id}")
        print(f"🔔 [NOTIFICATION] Current username: {current_user.username}")
        
        try:
            notification_service = NotificationService(db)
            print("🔔 [NOTIFICATION] NotificationService created")
            
            chat = chat_service.get_chat(chat_id)
            print(f"🔔 [NOTIFICATION] Chat found: {chat is not None}")
            
            if chat:
                print(f"🔔 [NOTIFICATION] Participants count: {len(chat.participants)}")
                for idx, p in enumerate(chat.participants):
                    print(f"🔔 [NOTIFICATION] Participant {idx}: user_id={p.user_id}")
                
                for participant in chat.participants:
                    print(f"🔔 [NOTIFICATION] Checking {participant.user_id} vs {current_user.id}")
                    if participant.user_id != current_user.id:
                        print(f"🔔 [NOTIFICATION] Creating notification for {participant.user_id}")
                        notification_service.create_notification(
                            user_id=participant.user_id,
                            chat_id=chat_id,
                            title="Новое сообщение",
                            message=f"{current_user.username}: {message.content[:50]}",
                            type="message"
                        )
                        print(f"✅ [NOTIFICATION] Notification created for {participant.user_id}")
                    else:
                        print(f"🔔 [NOTIFICATION] Skipping sender {participant.user_id}")
            else:
                print(f"❌ [NOTIFICATION] Chat {chat_id} not found!")
                
        except Exception as notif_error:
            print(f"❌ [NOTIFICATION] Exception: {notif_error}")
            import traceback
            traceback.print_exc()
        
        print("=" * 60)
        
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
                    "is_image": getattr(message, 'is_image', False),
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
            
            import asyncio
            asyncio.create_task(
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

@router.post("/{chat_id}/messages/read/all")
def mark_all_messages_as_read(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    message_service = MessageService(db)
    chat_service = ChatService(db)
    
    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    
    count = message_service.mark_all_as_read(chat_id, current_user.id)
    db.commit()
    
    import asyncio
    asyncio.create_task(
        manager.broadcast_to_chat(
            {
                "type": "messages_read",
                "chat_id": chat_id,
                "user_id": current_user.id
            },
            chat_id=chat_id,
            exclude_user_id=current_user.id
        )
    )
    
    return {"status": "ok", "marked_count": count}
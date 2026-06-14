from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import json
import asyncio
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
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
    
    try:
        message_service = MessageService(db)
        chat_service = ChatService(db)
        
        # Get chat for type check
        chat = chat_service.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        
        # Channel permission: only creator can post messages
        if chat.chat_type == "channel":
            if chat.created_by != current_user.id and current_user.username != "admin":
                raise HTTPException(status_code=403, detail="Only channel creator can post messages")
        
        if not chat_service.is_participant(chat_id, current_user.id):
            print(f"⚠️ User {current_user.id} not in participants, adding...")
            chat_service.add_participant(chat_id, current_user.id)
            db.flush()
        
        images_json = json.dumps(message_data.images) if message_data.images else None

        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content,
            is_image=message_data.is_image,
            images=images_json,
            reply_to_id=message_data.reply_to_id if hasattr(message_data, 'reply_to_id') else None
        )
        
        db.commit()
        db.refresh(message)
        
        print(f"✅ Message {message.id} COMMITTED to DB")

        images_list = json.loads(message.images) if message.images else None
        
        # Broadcast via WebSocket
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
                    "images": images_list,
                    "is_read": message.is_read
                }
            },
            chat_id=chat_id,
            exclude_user_id=current_user.id
        )
        
        return MessageResponse(
            id=message.id,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            content=message.content,
            sticker_id=message.sticker_id,
            is_sticker=message.is_sticker,
            is_image=message.is_image,
            images=json.loads(message.images) if message.images else None,
            reply_to_id=message.reply_to_id,
            created_at=message.created_at,
            is_read=message.is_read
        )
        
    except HTTPException:
        raise
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
        
        result = []
        for msg in messages:
            response = MessageResponse(
                id=msg.id,
                chat_id=msg.chat_id,
                sender_id=msg.sender_id,
                content=msg.content,
                sticker_id=msg.sticker_id,
                is_sticker=msg.is_sticker,
                is_image=msg.is_image,
                images=json.loads(msg.images) if msg.images else None,
                reply_to_id=msg.reply_to_id,
                created_at=msg.created_at,
                is_read=msg.is_read
            )
            result.append(response)
        
        print(f"📨 Loaded {len(result)} messages for user {current_user.id}")
        
        return result
        
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
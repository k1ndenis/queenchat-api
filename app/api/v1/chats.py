from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.core.websocket import manager, get_current_user_ws
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.models.chat import ChatCreate, ChatResponse, ChatDeleteResponse
from app.models.message import MessageCreate, MessageResponse
from app.repositories.auth_repository import AuthRepository

router = APIRouter()


def validate_chat_id(chat_id: str):
    if not chat_id or chat_id == "undefined" or chat_id == "null":
        raise HTTPException(status_code=400, detail="Invalid chat ID")
    return chat_id

@router.websocket("/ws/global")
async def global_websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    user = await get_current_user_ws(websocket, token, db)
    if not user:
        return
    
    await manager.connect_global(user.id, websocket)
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_global(user.id)

@router.websocket("/ws/{chat_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    chat_id: str,
    token: str = Query(...),
    db: Session = Depends(get_db)
):
    try:
        chat_id = validate_chat_id(chat_id)
    except HTTPException:
        await websocket.close(code=4000, reason="Invalid chat ID")
        return
    
    user = await get_current_user_ws(websocket, token, db)
    if not user:
        return
    
    chat_service = ChatService(db)
    
    if not chat_service.is_participant(chat_id, user.id):
        try:
            chat_service.add_participant(chat_id, user.id)
            db.commit()
            print(f"✅ WebSocket: Auto-added user {user.id} to chat {chat_id}")
        except Exception as e:
            print(f"❌ WebSocket: Failed to add participant: {e}")
            await websocket.close(code=4005, reason="Not a participant")
            return
    
    await manager.connect(chat_id, user.id, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            
            await manager.broadcast_to_chat(
                {
                    "type": "new_message",
                    "message": data.get("message", data)
                },
                chat_id=chat_id,
                exclude_user_id=user.id
            )
    except WebSocketDisconnect:
        manager.disconnect(chat_id, user.id)
        await manager.broadcast_to_chat(
            {"type": "user_left", "user_id": user.id},
            chat_id=chat_id
        )

@router.get("/{chat_id}", response_model=ChatResponse)
def get_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    chat_id = validate_chat_id(chat_id)
    
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if not service.is_participant(chat_id, current_user.id):
        try:
            service.add_participant(chat_id, current_user.id)
            db.commit()
            print(f"✅ GET chat: Auto-added user {current_user.id} to chat {chat_id}")
        except Exception as e:
            print(f"❌ GET chat: Failed to add participant: {e}")
            db.rollback()
    
    return chat

@router.delete("/{chat_id}", response_model=ChatDeleteResponse)
def delete_chat(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatDeleteResponse:
    chat_id = validate_chat_id(chat_id)
    
    service = ChatService(db)
    chat = service.get_chat(chat_id)
    
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    if not service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    
    service.delete_chat(chat_id)
    
    return ChatDeleteResponse(id=chat_id)

@router.post("/", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
def create_chat(
    chat_data: ChatCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> ChatResponse:
    service = ChatService(db)
    
    try:
        if chat_data.is_group:
            all_participants = list(set(chat_data.participant_ids + [current_user.id]))
            chat = service.create_chat(
                name=chat_data.name,
                is_group=True,
                created_by=current_user.id,
                participant_ids=all_participants
            )
        else:
            other_username = chat_data.participant_ids[0] if chat_data.participant_ids else None
            if not other_username:
                raise HTTPException(status_code=400, detail="Username required")
            
            auth_repo = AuthRepository(db)
            other_user = auth_repo.get_by_username(other_username)
            if not other_user:
                raise HTTPException(status_code=404, detail=f"User '{other_username}' not found")
            
            existing = service.repo.get_existing_private_chat(current_user.id, other_user.id)
            if existing:
                return service.get_chat(existing.id)
            
            chat = service.create_chat(
                name=None,
                is_group=False,
                created_by=current_user.id,
                participant_ids=[current_user.id, other_user.id]
            )
        
        db.commit()
        return chat
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ Error creating chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=List[ChatResponse])
def get_user_chats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[ChatResponse]:
    service = ChatService(db)
    chats = service.get_user_chats(current_user.id)
    return chats

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
        
        if not chat_service.is_participant(chat_id, current_user.id):
            chat_service.add_participant(chat_id, current_user.id)
            db.flush()
        
        message = message_service.create_message(
            chat_id=chat_id,
            sender_id=current_user.id,
            content=message_data.content
        )
        
        db.commit()
        db.refresh(message)
        
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
            
            from app.core.websocket import manager
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

@router.post("/{chat_id}/participants/{user_id}")
def add_participant(
    chat_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    service = ChatService(db)
    
    try:
        chat = service.get_chat(chat_id)
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        
        if not chat.is_group:
            if service.is_participant(chat_id, user_id):
                return {"message": "Participant already in chat"}
            
            service.add_participant(chat_id, user_id)
            db.commit()  # ✅ Commit
            print(f"✅ Added participant {user_id} to private chat {chat_id}")
            return {"message": "Participant added successfully"}
        
        if chat.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Only chat creator can add participants")
        
        if service.is_participant(chat_id, user_id):
            return {"message": "Participant already in chat"}
        
        service.add_participant(chat_id, user_id)
        db.commit()
        print(f"✅ Added participant {user_id} to group chat {chat_id}")
        return {"message": "Participant added successfully"}
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error adding participant: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{chat_id}/participants/{user_id}")
def remove_participant(
    chat_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    service = ChatService(db)
    
    try:
        chat = service.get_chat(chat_id)
        if not chat or chat.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="Only chat creator can remove participants")
        
        service.remove_participant(chat_id, user_id)
        db.commit()
        return {"message": "Participant removed successfully"}
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error removing participant: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/debug/chat/{chat_id}/status")
def debug_chat_status(
    chat_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_service = ChatService(db)
    message_service = MessageService(db)
    
    chat = chat_service.get_chat(chat_id)
    if not chat:
        return {"error": "Chat not found"}
    
    participants = [p.user_id for p in chat.participants]
    messages_count = len(message_service.get_chat_messages(chat_id, limit=1000, offset=0))
    
    return {
        "chat_id": chat_id,
        "is_group": chat.is_group,
        "created_by": chat.created_by,
        "participants": participants,
        "current_user_in_participants": current_user.id in participants,
        "messages_count": messages_count,
        "current_user": current_user.id
    }
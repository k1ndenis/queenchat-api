from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import asyncio

from app.core.websocket import manager, get_current_user_ws
from app.core.dependency import get_db, get_current_user
from app.core.database import UserORM as User
from app.services.chat_service import ChatService
from app.services.message_service import MessageService
from app.models.chat import ChatCreate, ChatResponse
from app.models.message import MessageCreate, MessageResponse
from app.repositories.auth_repository import AuthRepository

router = APIRouter()


def validate_chat_id(chat_id: str):
    if not chat_id or chat_id == "undefined" or chat_id == "null":
        raise HTTPException(status_code=400, detail="Invalid chat ID")
    return chat_id


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
        await websocket.close(code=4005, reason="Not a participant")
        return
    
    await manager.connect(chat_id, user.id, websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            
            message_service = MessageService(db)
            message = await message_service.create_message(
                chat_id=chat_id,
                sender_id=user.id,
                content=data.get("content")
            )
            
            await manager.broadcast_to_chat(
                {
                    "type": "new_message",
                    "message": {
                        "id": message.id,
                        "sender_id": message.sender_id,
                        "sender_name": user.username,
                        "content": message.content,
                        "created_at": message.created_at,
                        "chat_id": chat_id
                    }
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
        raise HTTPException(status_code=403, detail="Not a participant")
    
    return chat

@router.post("/", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
def create_chat(
        chat_data: ChatCreate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> ChatResponse:
        service = ChatService(db)
        
        if chat_data.is_group:
            chat = service.create_chat(
                name=chat_data.name,
                is_group=True,
                created_by=current_user.id,
                participant_ids=chat_data.participant_ids
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
                participant_ids=[other_user.id]
            )
        
        return chat

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
    
    service = MessageService(db)
    
    chat_service = ChatService(db)
    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    
    message = service.create_message(
        chat_id=chat_id,
        sender_id=current_user.id,
        content=message_data.content
    )
    
    await manager.broadcast_to_chat(
        {
            "type": "new_message",
            "message": {
                "id": message.id,
                "sender_id": message.sender_id,
                "sender_name": current_user.username,
                "content": message.content,
                "created_at": message.created_at,
                "chat_id": chat_id
            }
        },
        chat_id=chat_id,
        exclude_user_id=current_user.id
    )
    
    return message

@router.get("/{chat_id}/messages", response_model=List[MessageResponse])
def get_messages(
    chat_id: str,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> List[MessageResponse]:
    chat_id = validate_chat_id(chat_id)
    
    service = MessageService(db)
    
    chat_service = ChatService(db)
    if not chat_service.is_participant(chat_id, current_user.id):
        raise HTTPException(status_code=403, detail="Not a participant")
    
    messages = service.get_chat_messages(chat_id, limit=limit, offset=offset)
    return messages

@router.post("/{chat_id}/participants/{user_id}")
def add_participant(
    chat_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    service = ChatService(db)
    
    chat = service.get_chat(chat_id)
    if not chat or chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only chat creator can add participants")
    
    service.add_participant(chat_id, user_id)
    return {"message": "Participant added successfully"}

@router.delete("/{chat_id}/participants/{user_id}")
def remove_participant(
    chat_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    chat_id = validate_chat_id(chat_id)
    
    service = ChatService(db)
    
    chat = service.get_chat(chat_id)
    if not chat or chat.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only chat creator can remove participants")
    
    service.remove_participant(chat_id, user_id)
    return {"message": "Participant removed successfully"}
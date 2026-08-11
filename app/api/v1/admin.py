"""Administrative API.  This router is deliberately isolated from chat APIs."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Integer, cast, func, or_
from sqlalchemy.orm import Session

from app.api.v1.notifications import delete_fcm_token
from app.core.database import (
    AdminAuditLogORM, ChatBackgroundPreferenceORM, ChatORM, ChatParticipantORM,
    FileORM, MessageCommentORM, MessageORM, MessageReactionORM,
    ReactionNotificationORM, UserORM,
)
from app.core.dependency import get_db, require_admin
from app.core.redis import redis_cache
from app.core.websocket import manager

router = APIRouter(dependencies=[Depends(require_admin)])
MAX_PAGE_SIZE = 100
AnalyticsPeriod = Literal["24h", "7d", "30d", "1y", "all"]


class RoleRequest(BaseModel):
    role: Literal["user", "admin"]


class ConfirmationRequest(BaseModel):
    confirmation: str = Field(pattern="^DELETE$")


class BlockRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


def _page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        raise HTTPException(422, "page must be at least 1")
    return page, min(max(page_size, 1), MAX_PAGE_SIZE)


def _audit(db: Session, admin: UserORM, action: str, target_type: str, target_id: str, metadata: dict | None = None):
    db.add(AdminAuditLogORM(
        id=str(uuid.uuid4()), admin_user_id=admin.id, action=action,
        target_type=target_type, target_id=str(target_id), metadata_json=metadata or {},
        created_at=int(time.time()),
    ))


def _user_summary(db: Session, user: UserORM) -> dict:
    chat_count = db.query(func.count(ChatParticipantORM.id)).filter(ChatParticipantORM.user_id == user.id).scalar() or 0
    message_count = db.query(func.count(MessageORM.id)).filter(MessageORM.sender_id == user.id).scalar() or 0
    return {
        "id": user.id, "display_name": user.display_name, "username": user.username,
        "avatar": user.avatar, "role": user.role, "created_at": user.created_at,
        "is_blocked": user.is_blocked, "status": "blocked" if user.is_blocked else "active",
        "chat_count": chat_count, "message_count": message_count,
    }


def _chat_summary(db: Session, chat: ChatORM) -> dict:
    return {
        "id": chat.id, "type": chat.chat_type, "name": chat.name, "avatar": chat.avatar,
        "created_at": chat.created_at, "created_by": chat.created_by,
        "participants_count": db.query(func.count(ChatParticipantORM.id)).filter(ChatParticipantORM.chat_id == chat.id).scalar() or 0,
        "messages_count": db.query(func.count(MessageORM.id)).filter(MessageORM.chat_id == chat.id).scalar() or 0,
    }


def _clear_user_cache(user: UserORM):
    # These are the documented per-user cache keys; no global Redis flush.
    redis_cache.delete("all_users")
    redis_cache.delete_pattern("all_users_exclude_*")
    redis_cache.delete(f"user_profile:{user.username}")
    redis_cache.delete(f"user:{user.id}")
    redis_cache.delete(f"user_chats:{user.id}")


def _safe_unlink_file(record: FileORM):
    """Only remove an uploaded file when its resolved path is below UPLOAD_ROOT."""
    root = Path(os.getenv("UPLOAD_ROOT", "/app/uploads")).resolve()
    try:
        path = Path(record.file_path).resolve()
        path.relative_to(root)
        if path.is_file():
            path.unlink()
    except (OSError, ValueError):
        # DB cleanup must not become arbitrary filesystem deletion.
        pass


def _delete_messages(db: Session, message_ids: list[str]):
    if not message_ids:
        return
    # Null self references before deletion; existing messages outside the set can
    # safely remain as forwarded/reply-less messages.
    db.query(MessageORM).filter(MessageORM.reply_to_id.in_(message_ids)).update({"reply_to_id": None}, synchronize_session=False)
    db.query(MessageORM).filter(MessageORM.forwarded_from_message_id.in_(message_ids)).update({"forwarded_from_message_id": None}, synchronize_session=False)
    db.query(ReactionNotificationORM).filter(ReactionNotificationORM.message_id.in_(message_ids)).delete(synchronize_session=False)
    db.query(MessageReactionORM).filter(MessageReactionORM.message_id.in_(message_ids)).delete(synchronize_session=False)
    db.query(MessageCommentORM).filter(MessageCommentORM.message_id.in_(message_ids)).delete(synchronize_session=False)
    db.query(MessageORM).filter(MessageORM.id.in_(message_ids)).delete(synchronize_session=False)


def _delete_chat_records(db: Session, chat: ChatORM):
    """Delete chat dependencies in FK-safe order inside the caller transaction."""
    message_ids = [row[0] for row in db.query(MessageORM.id).filter(MessageORM.chat_id == chat.id).all()]
    for record in db.query(FileORM).filter(FileORM.chat_id == chat.id).all():
        _safe_unlink_file(record)
    db.query(FileORM).filter(FileORM.chat_id == chat.id).delete(synchronize_session=False)
    db.query(ReactionNotificationORM).filter(ReactionNotificationORM.chat_id == chat.id).delete(synchronize_session=False)
    db.query(MessageCommentORM).filter(MessageCommentORM.channel_id == chat.id).delete(synchronize_session=False)
    _delete_messages(db, message_ids)
    db.query(ChatBackgroundPreferenceORM).filter(ChatBackgroundPreferenceORM.chat_id == chat.id).delete(synchronize_session=False)
    db.query(ChatParticipantORM).filter(ChatParticipantORM.chat_id == chat.id).delete(synchronize_session=False)
    db.delete(chat)


def _utc_month_start(timestamp: int) -> int:
    value = datetime.fromtimestamp(timestamp, timezone.utc)
    return int(datetime(value.year, value.month, 1, tzinfo=timezone.utc).timestamp())


def _add_months(timestamp: int, months: int) -> int:
    value = datetime.fromtimestamp(timestamp, timezone.utc)
    month = value.month - 1 + months
    return int(datetime(value.year + month // 12, month % 12 + 1, 1, tzinfo=timezone.utc).timestamp())


def _fixed_buckets(now: int, period: AnalyticsPeriod) -> tuple[list[int], int, str]:
    """Return UTC-aligned bucket starts, their duration, and response granularity."""
    if period == "24h":
        size = 3600; end = (now // size + 1) * size
        return [end - size * offset for offset in range(24, 0, -1)], size, "hour"
    if period in {"7d", "30d"}:
        size = 86400; end = (now // size + 1) * size; count = 7 if period == "7d" else 30
        return [end - size * offset for offset in range(count, 0, -1)], size, "day"
    raise ValueError("Not a fixed period")


def _aggregate_fixed(db: Session, model, starts: list[int], size: int) -> dict[int, int]:
    # CAST(integer / integer AS integer) is portable between PostgreSQL and SQLite.
    # It groups in the database; zero buckets are filled by the caller.
    start, end = starts[0], starts[-1] + size
    bucket = cast(model.created_at / size, Integer) * size
    rows = db.query(bucket.label("bucket"), func.count(model.id)).filter(
        model.created_at >= start, model.created_at < end
    ).group_by(bucket).all()
    return {int(key): int(value) for key, value in rows}


def _aggregate_months(db: Session, model, start: int, end: int) -> dict[int, int]:
    # Calendar months must not be approximated with a number of seconds.
    if db.bind.dialect.name == "sqlite":
        month = func.strftime("%Y-%m", model.created_at, "unixepoch")
        rows = db.query(month, func.count(model.id)).filter(model.created_at >= start, model.created_at < end).group_by(month).all()
        return {int(datetime.strptime(key, "%Y-%m").replace(tzinfo=timezone.utc).timestamp()): int(value) for key, value in rows}
    month = func.date_trunc("month", func.to_timestamp(model.created_at))
    rows = db.query(month, func.count(model.id)).filter(model.created_at >= start, model.created_at < end).group_by(month).all()
    return {int(key.replace(tzinfo=timezone.utc).timestamp()): int(value) for key, value in rows}


@router.get("/analytics")
def analytics(period: AnalyticsPeriod = Query("7d"), db: Session = Depends(get_db)):
    """UTC analytics with contiguous buckets and two grouped aggregate queries."""
    now = int(time.time())
    if period == "1y":
        current = _utc_month_start(now)
        starts = [_add_months(current, offset) for offset in range(-11, 1)]
        end = _add_months(current, 1)
        registrations = _aggregate_months(db, UserORM, starts[0], end)
        messages = _aggregate_months(db, MessageORM, starts[0], end)
        granularity = "month"
    elif period == "all":
        first_user = db.query(func.min(UserORM.created_at)).scalar()
        first_message = db.query(func.min(MessageORM.created_at)).scalar()
        first = min((value for value in (first_user, first_message) if value is not None), default=now)
        age_days = max(1, (now - first) // 86400 + 1)
        if age_days <= 36:
            starts, size, granularity = _fixed_buckets(now, "30d")
            starts = [starts[-1] - size * offset for offset in range(min(age_days, 36) - 1, -1, -1)]
            registrations = _aggregate_fixed(db, UserORM, starts, size); messages = _aggregate_fixed(db, MessageORM, starts, size)
        elif age_days <= 90:
            size = 7 * 86400; end = (now // size + 1) * size; count = min(14, (age_days + 6) // 7)
            starts = [end - size * offset for offset in range(count, 0, -1)]
            registrations = _aggregate_fixed(db, UserORM, starts, size); messages = _aggregate_fixed(db, MessageORM, starts, size); granularity = "week"
        else:
            current = _utc_month_start(now); first_month = _utc_month_start(first)
            months = (datetime.fromtimestamp(current, timezone.utc).year - datetime.fromtimestamp(first_month, timezone.utc).year) * 12 + datetime.fromtimestamp(current, timezone.utc).month - datetime.fromtimestamp(first_month, timezone.utc).month + 1
            step = max(1, (months + 35) // 36)
            raw_starts = [_add_months(first_month, offset) for offset in range(0, months, step)]
            # Aggregate by calendar month then fold into <=36 wider month buckets in Python.
            monthly_users = _aggregate_months(db, UserORM, first_month, _add_months(current, 1))
            monthly_messages = _aggregate_months(db, MessageORM, first_month, _add_months(current, 1))
            starts = raw_starts; end = _add_months(current, 1); registrations = {}; messages = {}
            for index, bucket_start in enumerate(starts):
                bucket_end = starts[index + 1] if index + 1 < len(starts) else end
                registrations[bucket_start] = sum(value for month, value in monthly_users.items() if bucket_start <= month < bucket_end)
                messages[bucket_start] = sum(value for month, value in monthly_messages.items() if bucket_start <= month < bucket_end)
            granularity = "month"
    else:
        starts, size, granularity = _fixed_buckets(now, period)
        registrations = _aggregate_fixed(db, UserORM, starts, size)
        messages = _aggregate_fixed(db, MessageORM, starts, size)
        end = starts[-1] + size

    points = [{"timestamp": start, "label": datetime.fromtimestamp(start, timezone.utc).isoformat(),
               "registrations": registrations.get(start, 0), "messages": messages.get(start, 0)} for start in starts]
    return {"period": period, "granularity": granularity, "from": starts[0], "to": end,
            "totals": {"registrations": sum(point["registrations"] for point in points), "messages": sum(point["messages"] for point in points)}, "points": points}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    now = int(time.time()); day = now - 86400
    total = db.query(func.count(UserORM.id)).scalar() or 0
    blocked = db.query(func.count(UserORM.id)).filter(UserORM.is_blocked.is_(True)).scalar() or 0
    chats = db.query(ChatORM.chat_type, func.count(ChatORM.id)).group_by(ChatORM.chat_type).all()
    chat_counts = dict(chats)
    return {
        "users_total": total, "users_active": total - blocked, "users_blocked": blocked,
        "users_registered_today": db.query(func.count(UserORM.id)).filter(UserORM.created_at >= day).scalar() or 0,
        "users_registered_7d": db.query(func.count(UserORM.id)).filter(UserORM.created_at >= now - 7 * 86400).scalar() or 0,
        "users_registered_30d": db.query(func.count(UserORM.id)).filter(UserORM.created_at >= now - 30 * 86400).scalar() or 0,
        "private_chats_total": chat_counts.get("private", 0), "groups_total": chat_counts.get("group", 0), "channels_total": chat_counts.get("channel", 0),
        "messages_total": db.query(func.count(MessageORM.id)).scalar() or 0,
        "messages_today": db.query(func.count(MessageORM.id)).filter(MessageORM.created_at >= day).scalar() or 0,
        "messages_7d": db.query(func.count(MessageORM.id)).filter(MessageORM.created_at >= now - 7 * 86400).scalar() or 0,
    }


@router.get("/users")
def list_users(q: str = "", page: int = 1, page_size: int = 30, status: Optional[Literal["active", "blocked"]] = None, role: Optional[Literal["user", "admin"]] = None, sort: Literal["created_at", "username", "message_count", "chat_count"] = "created_at", order: Literal["asc", "desc"] = "desc", db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size)
    query = db.query(UserORM)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(UserORM.display_name.ilike(term), UserORM.username.ilike(term), UserORM.id.ilike(term)))
    if status: query = query.filter(UserORM.is_blocked.is_(status == "blocked"))
    if role: query = query.filter(UserORM.role == role)
    total = query.count()
    # Counts are computed per listed user to keep query portable across current SQLite tests/PostgreSQL.
    users = query.order_by((UserORM.username if sort == "username" else UserORM.created_at).asc() if order == "asc" else (UserORM.username if sort == "username" else UserORM.created_at).desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [_user_summary(db, user) for user in users]
    if sort in {"message_count", "chat_count"}:
        items.sort(key=lambda item: item[sort], reverse=order == "desc")
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.get("/users/{user_id}")
def get_user(user_id: str, db: Session = Depends(get_db)):
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    result = _user_summary(db, user)
    memberships = db.query(ChatORM).join(ChatParticipantORM).filter(ChatParticipantORM.user_id == user.id).all()
    result.update({
        "private_memberships": sum(chat.chat_type == "private" for chat in memberships),
        "group_memberships": sum(chat.chat_type == "group" for chat in memberships),
        "channel_memberships": sum(chat.chat_type == "channel" for chat in memberships),
        "chats": [{"id": chat.id, "type": chat.chat_type, "name": chat.name} for chat in memberships],
    })
    return result


@router.post("/users/{user_id}/block")
async def block_user(user_id: str, payload: BlockRequest = BlockRequest(), admin: UserORM = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == admin.id: raise HTTPException(400, "You cannot block yourself")
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if not user.is_blocked:
        user.is_blocked = True; user.blocked_at = int(time.time()); user.blocked_reason = payload.reason
        _audit(db, admin, "USER_BLOCK", "user", user.id, {"reason": payload.reason} if payload.reason else {})
        db.commit(); _clear_user_cache(user); delete_fcm_token(user.id); await manager.close_user_connections(user.id, "Account is blocked")
    return _user_summary(db, user)


@router.post("/users/{user_id}/unblock")
def unblock_user(user_id: str, admin: UserORM = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if user.is_blocked:
        user.is_blocked = False; user.blocked_at = None; user.blocked_reason = None
        _audit(db, admin, "USER_UNBLOCK", "user", user.id); db.commit(); _clear_user_cache(user)
    return _user_summary(db, user)


@router.patch("/users/{user_id}/role")
def change_role(user_id: str, payload: RoleRequest, admin: UserORM = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if user.role == "admin" and payload.role != "admin":
        admins = db.query(func.count(UserORM.id)).filter(UserORM.role == "admin").scalar() or 0
        if admins <= 1: raise HTTPException(400, "Cannot remove the last administrator")
    old_role = user.role; user.role = payload.role
    _audit(db, admin, "USER_ROLE_CHANGE", "user", user.id, {"from": old_role, "to": payload.role})
    db.commit(); _clear_user_cache(user)
    return _user_summary(db, user)


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, payload: ConfirmationRequest, admin: UserORM = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == admin.id: raise HTTPException(400, "You cannot delete yourself")
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if user.role == "admin" and (db.query(func.count(UserORM.id)).filter(UserORM.role == "admin").scalar() or 0) <= 1:
        raise HTTPException(400, "Cannot delete the last administrator")
    try:
        own_message_ids = [row[0] for row in db.query(MessageORM.id).filter(MessageORM.sender_id == user.id).all()]
        db.query(MessageReactionORM).filter(MessageReactionORM.user_id == user.id).delete(synchronize_session=False)
        db.query(MessageCommentORM).filter(MessageCommentORM.user_id == user.id).delete(synchronize_session=False)
        db.query(ReactionNotificationORM).filter(or_(ReactionNotificationORM.user_id == user.id, ReactionNotificationORM.reaction_user_id == user.id)).delete(synchronize_session=False)
        _delete_messages(db, own_message_ids)
        for record in db.query(FileORM).filter(FileORM.user_id == user.id).all(): _safe_unlink_file(record)
        db.query(FileORM).filter(FileORM.user_id == user.id).delete(synchronize_session=False)
        db.query(ChatBackgroundPreferenceORM).filter(ChatBackgroundPreferenceORM.updated_by_user_id == user.id).update({"updated_by_user_id": None}, synchronize_session=False)
        db.query(MessageORM).filter(MessageORM.forwarded_from_user_id == user.id).update({"forwarded_from_user_id": None}, synchronize_session=False)
        db.query(ChatParticipantORM).filter(ChatParticipantORM.user_id == user.id).delete(synchronize_session=False)
        _audit(db, admin, "USER_DELETE", "user", user.id, {"username": user.username})
        db.delete(user); db.commit()
    except Exception:
        db.rollback(); raise HTTPException(409, "Unable to safely delete user")
    _clear_user_cache(user); delete_fcm_token(user.id); await manager.close_user_connections(user.id, "Account deleted")
    return {"status": "deleted", "id": user_id}


@router.get("/chats")
def list_chats(type: Optional[Literal["private", "group", "channel"]] = None, q: str = "", page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size); query = db.query(ChatORM)
    if type: query = query.filter(ChatORM.chat_type == type)
    if q.strip(): query = query.filter(ChatORM.name.ilike(f"%{q.strip()}%"))
    total = query.count(); chats = query.order_by(ChatORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_chat_summary(db, chat) for chat in chats], "page": page, "page_size": page_size, "total": total}


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, db: Session = Depends(get_db)):
    chat = db.get(ChatORM, chat_id)
    if not chat: raise HTTPException(404, "Chat not found")
    result = _chat_summary(db, chat)
    result["participants"] = [{"id": user.id, "username": user.username, "display_name": user.display_name, "avatar": user.avatar} for user in chat.participants]
    return result


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, payload: ConfirmationRequest, admin: UserORM = Depends(require_admin), db: Session = Depends(get_db)):
    chat = db.get(ChatORM, chat_id)
    if not chat: raise HTTPException(404, "Chat not found")
    try:
        _delete_chat_records(db, chat); _audit(db, admin, "CHAT_DELETE", "chat", chat_id); db.commit()
    except Exception:
        db.rollback(); raise HTTPException(409, "Unable to safely delete chat")
    return {"status": "deleted", "id": chat_id}


@router.get("/chats/{chat_id}/participants")
def list_participants(chat_id: str, db: Session = Depends(get_db)):
    chat = db.get(ChatORM, chat_id)
    if not chat: raise HTTPException(404, "Chat not found")
    if chat.chat_type == "private": raise HTTPException(400, "Private chat participants cannot be managed")
    return [{"id": user.id, "username": user.username, "display_name": user.display_name, "avatar": user.avatar} for user in chat.participants]


@router.delete("/chats/{chat_id}/participants/{user_id}")
def remove_participant(chat_id: str, user_id: str, admin: UserORM = Depends(require_admin), db: Session = Depends(get_db)):
    chat = db.get(ChatORM, chat_id)
    if not chat: raise HTTPException(404, "Chat not found")
    if chat.chat_type == "private": raise HTTPException(400, "Private chat participants cannot be managed")
    participant = db.query(ChatParticipantORM).filter(ChatParticipantORM.chat_id == chat_id, ChatParticipantORM.user_id == user_id).first()
    if not participant: raise HTTPException(404, "Participant not found")
    db.delete(participant); _audit(db, admin, "PARTICIPANT_REMOVE", "chat", chat_id, {"user_id": user_id}); db.commit()
    return {"status": "removed"}


@router.get("/audit")
def audit(admin: Optional[str] = None, action: Optional[str] = None, target: Optional[str] = None, date_from: Optional[int] = None, date_to: Optional[int] = None, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size); query = db.query(AdminAuditLogORM)
    if admin: query = query.filter(AdminAuditLogORM.admin_user_id == admin)
    if action: query = query.filter(AdminAuditLogORM.action == action)
    if target: query = query.filter(or_(AdminAuditLogORM.target_id == target, AdminAuditLogORM.target_type == target))
    if date_from is not None: query = query.filter(AdminAuditLogORM.created_at >= date_from)
    if date_to is not None: query = query.filter(AdminAuditLogORM.created_at <= date_to)
    total = query.count(); rows = query.order_by(AdminAuditLogORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    admins = {user.id: user for user in db.query(UserORM).filter(UserORM.id.in_([row.admin_user_id for row in rows])).all()} if rows else {}
    return {"items": [{"id": row.id, "admin_user_id": row.admin_user_id, "admin": {"id": admins[row.admin_user_id].id, "username": admins[row.admin_user_id].username, "display_name": admins[row.admin_user_id].display_name} if row.admin_user_id in admins else None, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "metadata": row.metadata_json, "created_at": row.created_at} for row in rows], "page": page, "page_size": page_size, "total": total}

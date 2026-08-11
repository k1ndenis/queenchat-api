"""Administrative API.  This router is deliberately isolated from chat APIs."""
from __future__ import annotations

import os
import time
import uuid
import shutil
import requests
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Integer, case, cast, func, literal, or_, union_all
from sqlalchemy.orm import Session

from app.api.v1.notifications import delete_fcm_token
from app.core.database import (
    AdminAuditLogORM, ChatBackgroundPreferenceORM, ChatORM, ChatParticipantORM,
    FileORM, MessageCommentORM, MessageORM, MessageReactionORM,
    PrivateChatInviteORM, PrivateSpaceInviteORM, PrivateSpaceSettingsORM,
    ReactionNotificationORM, SpaceMemoryORM, SpaceDateORM, SpaceNoteORM, UserORM,
)
from app.core.dependency import get_db, require_admin
from app.core.redis import redis_cache
from app.core.websocket import manager
from app.core.rate_limit import (ADMIN_MUTATION, ADMIN_READ, COMMENT_EVENTS,
    INVITE_CREATE, LOGIN_IP, MESSAGE_BURST, MESSAGE_SUSTAINED, REACTION_EVENTS,
    REGISTER_IP_BURST, REGISTER_IP_HOUR, WS_EVENTS, hit)

def _admin_read(admin: UserORM = Depends(require_admin)):
    hit(ADMIN_READ, admin.id)
    return admin


def _admin_mutation(admin: UserORM = Depends(require_admin)):
    hit(ADMIN_MUTATION, admin.id)
    return admin


router = APIRouter(dependencies=[Depends(_admin_read)])
MAX_PAGE_SIZE = 100
AnalyticsPeriod = Literal["24h", "7d", "30d", "90d", "1y", "all", "custom"]


@router.get("/monitoring-summary")
def monitoring_summary():
    """A deliberately small admin-only view; detailed telemetry stays in Grafana."""
    base = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
    queries = {
        "rps": "sum(rate(http_requests_total[5m]))",
        "p95": "histogram_quantile(.95, sum by(le) (rate(http_request_duration_seconds_bucket[5m])))",
        "errors_5xx_percent": "100 * sum(rate(http_requests_total{status_code=~'5..'}[5m])) / sum(rate(http_requests_total[5m]))",
        "cpu_percent": "100 * (1 - avg(rate(node_cpu_seconds_total{mode='idle'}[5m])))",
        "ram_available_percent": "100 * node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes",
        "disk_used_percent": "100 * (1-node_filesystem_avail_bytes{mountpoint='/'} / node_filesystem_size_bytes{mountpoint='/'})",
        "active_ws": "sum(queenchat_websocket_connections)",
        "api_up": "up{job='queenchat-api'}",
    }
    values: dict[str, float | None] = {key: None for key in queries}
    try:
        for key, query in queries.items():
            payload = requests.get(f"{base}/api/v1/query", params={"query": query}, timeout=1).json()
            result = payload.get("data", {}).get("result", [])
            values[key] = float(result[0]["value"][1]) if result else None
    except (requests.RequestException, ValueError, KeyError, IndexError):
        return {"available": False, "grafana_url": os.getenv("GRAFANA_PUBLIC_URL", "")}
    return {"available": True, "api": "healthy" if values["api_up"] == 1 else "unavailable", **values,
            "grafana_url": os.getenv("GRAFANA_PUBLIC_URL", "")}


class RoleRequest(BaseModel):
    role: Literal["user", "admin"]


class ConfirmationRequest(BaseModel):
    confirmation: str = Field(pattern="^DELETE$")


class UserDeleteRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)


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
    last_activity = db.query(func.max(MessageORM.created_at)).filter(MessageORM.chat_id == chat.id).scalar()
    space = db.get(PrivateSpaceSettingsORM, chat.id)
    return {
        "id": chat.id, "type": chat.chat_type, "name": chat.name, "avatar": chat.avatar,
        "created_at": chat.created_at, "created_by": chat.created_by,
        "participants_count": db.query(func.count(ChatParticipantORM.id)).filter(ChatParticipantORM.chat_id == chat.id).scalar() or 0,
        "messages_count": db.query(func.count(MessageORM.id)).filter(MessageORM.chat_id == chat.id).scalar() or 0,
        "last_activity": last_activity,
        "space_status": space.status if space else None,
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
    if period in {"7d", "30d", "90d"}:
        size = 86400; end = (now // size + 1) * size; count = {"7d": 7, "30d": 30, "90d": 90}[period]
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
def analytics(period: AnalyticsPeriod = Query("7d"), date_from: Optional[int] = None, date_to: Optional[int] = None, db: Session = Depends(get_db)):
    """UTC analytics, aggregated in SQL and returned with contiguous zero-filled buckets."""
    now = int(time.time())
    models = {"registrations": UserORM, "messages": MessageORM, "chats": ChatORM, "uploads": FileORM}
    if period == "custom":
        if date_from is None or date_to is None or date_to <= date_from:
            raise HTTPException(422, "custom analytics requires a valid date range")
        span = date_to - date_from
        if span > 366 * 86400:
            raise HTTPException(422, "custom range is limited to 366 days")
        size = 86400 if span <= 90 * 86400 else 7 * 86400
        start = (date_from // size) * size; end = ((date_to // size) + 1) * size
        starts = list(range(start, end, size)); granularity = "day" if size == 86400 else "week"
        aggregates = {name: _aggregate_fixed(db, model, starts, size) for name, model in models.items()}
    elif period == "1y":
        current = _utc_month_start(now)
        starts = [_add_months(current, offset) for offset in range(-11, 1)]
        end = _add_months(current, 1)
        aggregates = {name: _aggregate_months(db, model, starts[0], end) for name, model in models.items()}
        granularity = "month"
    elif period == "all":
        firsts = [db.query(func.min(model.created_at)).scalar() for model in models.values()]
        first = min((value for value in firsts if value is not None), default=now)
        first_month = _utc_month_start(first); current = _utc_month_start(now); end = _add_months(current, 1)
        months = (datetime.fromtimestamp(current, timezone.utc).year - datetime.fromtimestamp(first_month, timezone.utc).year) * 12 + datetime.fromtimestamp(current, timezone.utc).month - datetime.fromtimestamp(first_month, timezone.utc).month + 1
        # At most 36 monthly buckets: predictable cost for long-lived installations.
        step = max(1, (months + 35) // 36); starts = [_add_months(first_month, offset) for offset in range(0, months, step)]
        aggregates = {}
        for name, model in models.items():
            monthly = _aggregate_months(db, model, first_month, end); folded = {}
            for index, bucket_start in enumerate(starts):
                bucket_end = starts[index + 1] if index + 1 < len(starts) else end
                folded[bucket_start] = sum(value for month, value in monthly.items() if bucket_start <= month < bucket_end)
            aggregates[name] = folded
        granularity = "month"
    else:
        starts, size, granularity = _fixed_buckets(now, period)
        aggregates = {name: _aggregate_fixed(db, model, starts, size) for name, model in models.items()}
        end = starts[-1] + size

    points = [{"timestamp": start, "label": datetime.fromtimestamp(start, timezone.utc).isoformat(), **{name: values.get(start, 0) for name, values in aggregates.items()}} for start in starts]
    return {"period": period, "granularity": granularity, "from": starts[0], "to": end,
            "totals": {name: sum(point[name] for point in points) for name in models}, "points": points}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    now = int(time.time()); day = now - 86400
    total = db.query(func.count(UserORM.id)).scalar() or 0
    blocked = db.query(func.count(UserORM.id)).filter(UserORM.is_blocked.is_(True)).scalar() or 0
    chats = db.query(ChatORM.chat_type, func.count(ChatORM.id)).group_by(ChatORM.chat_type).all()
    chat_counts = dict(chats)
    file_count, file_bytes = db.query(func.count(FileORM.id), func.coalesce(func.sum(FileORM.file_size), 0)).one()
    invite_rows = list(db.query(PrivateChatInviteORM.accepted_at, PrivateChatInviteORM.revoked_at, PrivateChatInviteORM.expires_at).all()) + list(db.query(PrivateSpaceInviteORM.accepted_at, PrivateSpaceInviteORM.revoked_at, PrivateSpaceInviteORM.expires_at).all())
    invite_counts = {"active": 0, "accepted": 0, "expired": 0, "revoked": 0}
    for accepted_at, revoked_at, expires_at in invite_rows:
        invite_counts["revoked" if revoked_at else "accepted" if accepted_at else "expired" if expires_at <= now else "active"] += 1
    usage = shutil.disk_usage(os.getenv("UPLOAD_ROOT", "/app/uploads"))
    try: redis_healthy = bool(redis_cache.redis_client.ping())
    except Exception: redis_healthy = False
    active_users = set(manager.global_connections)
    for users in manager.active_connections.values(): active_users.update(users)
    return {
        "users_total": total, "users_active": total - blocked, "users_blocked": blocked,
        "users_registered_today": db.query(func.count(UserORM.id)).filter(UserORM.created_at >= day).scalar() or 0,
        "users_registered_7d": db.query(func.count(UserORM.id)).filter(UserORM.created_at >= now - 7 * 86400).scalar() or 0,
        "users_registered_30d": db.query(func.count(UserORM.id)).filter(UserORM.created_at >= now - 30 * 86400).scalar() or 0,
        "private_chats_total": chat_counts.get("private", 0), "groups_total": chat_counts.get("group", 0), "channels_total": chat_counts.get("channel", 0),
        "messages_total": db.query(func.count(MessageORM.id)).scalar() or 0,
        "messages_today": db.query(func.count(MessageORM.id)).filter(MessageORM.created_at >= day).scalar() or 0,
        "messages_7d": db.query(func.count(MessageORM.id)).filter(MessageORM.created_at >= now - 7 * 86400).scalar() or 0,
        "messages_30d": db.query(func.count(MessageORM.id)).filter(MessageORM.created_at >= now - 30 * 86400).scalar() or 0,
        "media": {"files": int(file_count or 0), "bytes": int(file_bytes or 0)},
        "invites": invite_counts,
        "spaces": {"active": db.query(func.count(PrivateSpaceSettingsORM.chat_id)).filter(PrivateSpaceSettingsORM.status == "active").scalar() or 0, "pending": db.query(func.count(PrivateSpaceSettingsORM.chat_id)).filter(PrivateSpaceSettingsORM.status == "pending").scalar() or 0},
        "realtime": {"websocket_connections": sum(len(x) for x in manager.global_connections.values()) + sum(len(sockets) for users in manager.active_connections.values() for sockets in users.values()), "websocket_users": len(active_users)},
        "system": {"disk_total": usage.total, "disk_free": usage.free, "uploads_path": "configured", "redis": redis_healthy, "database": True},
    }


def _page_result(items: list[dict], page: int, page_size: int, total: int) -> dict:
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def _message_summary(db: Session, message: MessageORM, include_content: bool = False) -> dict:
    return {"id": message.id, "chat_id": message.chat_id, "sender": {"id": message.sender.id, "username": message.sender.username, "display_name": message.sender.display_name, "avatar": message.sender.avatar}, "created_at": message.created_at, "edited_at": message.edited_at, "deleted_at": message.deleted_at, "content": message.content if include_content else (message.content or "")[:240], "is_sticker": message.is_sticker, "is_image": message.is_image, "has_media": bool(message.media or message.images), "reply_to_id": message.reply_to_id, "reactions_count": db.query(func.count(MessageReactionORM.id)).filter(MessageReactionORM.message_id == message.id).scalar() or 0, "comments_count": db.query(func.count(MessageCommentORM.id)).filter(MessageCommentORM.message_id == message.id, MessageCommentORM.deleted_at.is_(None)).scalar() or 0}


def _file_summary(file: FileORM) -> dict:
    return {"id": file.id, "filename": file.filename, "original_name": file.original_name, "mime_type": file.mime_type, "file_size": file.file_size, "user_id": file.user_id, "chat_id": file.chat_id, "created_at": file.created_at}


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
        "files_count": db.query(func.count(FileORM.id)).filter(FileORM.user_id == user.id).scalar() or 0,
        "storage_bytes": int(db.query(func.coalesce(func.sum(FileORM.file_size), 0)).filter(FileORM.user_id == user.id).scalar() or 0),
        "reactions_count": db.query(func.count(MessageReactionORM.id)).filter(MessageReactionORM.user_id == user.id).scalar() or 0,
        "comments_count": db.query(func.count(MessageCommentORM.id)).filter(MessageCommentORM.user_id == user.id).scalar() or 0,
        "invites_created": (db.query(func.count(PrivateChatInviteORM.id)).filter(PrivateChatInviteORM.creator_user_id == user.id).scalar() or 0) + (db.query(func.count(PrivateSpaceInviteORM.id)).filter(PrivateSpaceInviteORM.creator_user_id == user.id).scalar() or 0),
        "spaces": db.query(func.count(PrivateSpaceSettingsORM.chat_id)).join(ChatParticipantORM, ChatParticipantORM.chat_id == PrivateSpaceSettingsORM.chat_id).filter(ChatParticipantORM.user_id == user.id).scalar() or 0,
        "active_websocket_sessions": manager.connection_count(user.id),
        "devices": {"available": False, "count": None},
        "chats": [{"id": chat.id, "type": chat.chat_type, "name": chat.name} for chat in memberships],
    })
    return result


@router.get("/users/{user_id}/chats")
def user_chats(user_id: str, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size)
    if not db.get(UserORM, user_id): raise HTTPException(404, "User not found")
    query = db.query(ChatORM).join(ChatParticipantORM).filter(ChatParticipantORM.user_id == user_id)
    return _page_result([_chat_summary(db, chat) for chat in query.order_by(ChatORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()], page, page_size, query.count())


@router.get("/users/{user_id}/messages")
def user_messages(user_id: str, date_from: Optional[int] = None, date_to: Optional[int] = None, has_media: Optional[bool] = None, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size)
    if not db.get(UserORM, user_id): raise HTTPException(404, "User not found")
    query = db.query(MessageORM).filter(MessageORM.sender_id == user_id)
    if date_from is not None: query = query.filter(MessageORM.created_at >= date_from)
    if date_to is not None: query = query.filter(MessageORM.created_at <= date_to)
    if has_media is not None: query = query.filter(or_(MessageORM.media.is_not(None), MessageORM.images.is_not(None)) if has_media else MessageORM.media.is_(None), MessageORM.images.is_(None))
    return _page_result([_message_summary(db, row) for row in query.order_by(MessageORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()], page, page_size, query.count())


@router.get("/users/{user_id}/files")
def user_files(user_id: str, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size)
    if not db.get(UserORM, user_id): raise HTTPException(404, "User not found")
    query = db.query(FileORM).filter(FileORM.user_id == user_id)
    return _page_result([_file_summary(row) for row in query.order_by(FileORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()], page, page_size, query.count())


@router.get("/users/{user_id}/audit")
def user_admin_history(user_id: str, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size)
    query = db.query(AdminAuditLogORM).filter(AdminAuditLogORM.target_type == "user", AdminAuditLogORM.target_id == user_id)
    rows = query.order_by(AdminAuditLogORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page_result([{"id": row.id, "action": row.action, "created_at": row.created_at, "metadata": row.metadata_json} for row in rows], page, page_size, query.count())


@router.get("/users/{user_id}/delete-impact")
def user_delete_impact(user_id: str, db: Session = Depends(get_db)):
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    return {"username": user.username, "messages": db.query(func.count(MessageORM.id)).filter(MessageORM.sender_id == user.id).scalar() or 0, "chats": db.query(func.count(ChatParticipantORM.id)).filter(ChatParticipantORM.user_id == user.id).scalar() or 0, "files": db.query(func.count(FileORM.id)).filter(FileORM.user_id == user.id).scalar() or 0, "storage_bytes": int(db.query(func.coalesce(func.sum(FileORM.file_size), 0)).filter(FileORM.user_id == user.id).scalar() or 0), "invites": (db.query(func.count(PrivateChatInviteORM.id)).filter(PrivateChatInviteORM.creator_user_id == user.id).scalar() or 0) + (db.query(func.count(PrivateSpaceInviteORM.id)).filter(PrivateSpaceInviteORM.creator_user_id == user.id).scalar() or 0), "spaces": db.query(func.count(PrivateSpaceSettingsORM.chat_id)).join(ChatParticipantORM, ChatParticipantORM.chat_id == PrivateSpaceSettingsORM.chat_id).filter(ChatParticipantORM.user_id == user.id).scalar() or 0}


@router.post("/users/{user_id}/block")
async def block_user(user_id: str, payload: BlockRequest = BlockRequest(), admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
    if user_id == admin.id: raise HTTPException(400, "You cannot block yourself")
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if not user.is_blocked:
        user.is_blocked = True; user.blocked_at = int(time.time()); user.blocked_reason = payload.reason
        _audit(db, admin, "USER_BLOCK", "user", user.id, {"reason": payload.reason} if payload.reason else {})
        db.commit(); _clear_user_cache(user); delete_fcm_token(user.id); await manager.close_user_connections(user.id, "Account is blocked")
    return _user_summary(db, user)


@router.post("/users/{user_id}/unblock")
def unblock_user(user_id: str, admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if user.is_blocked:
        user.is_blocked = False; user.blocked_at = None; user.blocked_reason = None
        _audit(db, admin, "USER_UNBLOCK", "user", user.id); db.commit(); _clear_user_cache(user)
    return _user_summary(db, user)


@router.patch("/users/{user_id}/role")
def change_role(user_id: str, payload: RoleRequest, admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
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
async def delete_user(user_id: str, payload: UserDeleteRequest, admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
    if user_id == admin.id: raise HTTPException(400, "You cannot delete yourself")
    user = db.get(UserORM, user_id)
    if not user: raise HTTPException(404, "User not found")
    if payload.username != user.username:
        raise HTTPException(400, "Enter the target username to confirm deletion")
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
    result["participants"] = [{"id": user.id, "username": user.username, "display_name": user.display_name, "avatar": user.avatar, "joined_at": participant.joined_at} for participant in db.query(ChatParticipantORM).filter(ChatParticipantORM.chat_id == chat.id).all() for user in [db.get(UserORM, participant.user_id)] if user]
    return result


@router.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: str, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size)
    if not db.get(ChatORM, chat_id): raise HTTPException(404, "Chat not found")
    query = db.query(MessageORM).filter(MessageORM.chat_id == chat_id)
    return _page_result([_message_summary(db, row) for row in query.order_by(MessageORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()], page, page_size, query.count())


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, payload: ConfirmationRequest, admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
    chat = db.get(ChatORM, chat_id)
    if chat and chat.chat_type == "private": raise HTTPException(400, "Private chats require account-level moderation")
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
def remove_participant(chat_id: str, user_id: str, admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
    chat = db.get(ChatORM, chat_id)
    if not chat: raise HTTPException(404, "Chat not found")
    if chat.chat_type == "private": raise HTTPException(400, "Private chat participants cannot be managed")
    participant = db.query(ChatParticipantORM).filter(ChatParticipantORM.chat_id == chat_id, ChatParticipantORM.user_id == user_id).first()
    if not participant: raise HTTPException(404, "Participant not found")
    db.delete(participant); _audit(db, admin, "PARTICIPANT_REMOVE", "chat", chat_id, {"user_id": user_id}); db.commit()
    return {"status": "removed"}


@router.get("/messages")
def list_messages(user_id: Optional[str] = None, chat_id: Optional[str] = None, q: str = "", media: Optional[bool] = None, deleted: Optional[bool] = None, date_from: Optional[int] = None, date_to: Optional[int] = None, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size); query = db.query(MessageORM)
    if user_id: query = query.filter(MessageORM.sender_id == user_id)
    if chat_id: query = query.filter(MessageORM.chat_id == chat_id)
    if q.strip(): query = query.filter(MessageORM.content.ilike(f"%{q.strip()[:120]}%"))
    if media is True: query = query.filter(or_(MessageORM.media.is_not(None), MessageORM.images.is_not(None)))
    if deleted is not None: query = query.filter(MessageORM.deleted_at.is_not(None) if deleted else MessageORM.deleted_at.is_(None))
    if date_from is not None: query = query.filter(MessageORM.created_at >= date_from)
    if date_to is not None: query = query.filter(MessageORM.created_at <= date_to)
    total = query.count(); rows = query.order_by(MessageORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page_result([_message_summary(db, row) for row in rows], page, page_size, total)


@router.get("/messages/{message_id}")
def get_message(message_id: str, db: Session = Depends(get_db)):
    message = db.get(MessageORM, message_id)
    if not message: raise HTTPException(404, "Message not found")
    result = _message_summary(db, message, include_content=True)
    result["images"] = message.images
    result["media"] = message.media
    result["reactions"] = [{"emoji": row.emoji, "user": {"id": row.user.id, "username": row.user.username, "display_name": row.user.display_name}} for row in db.query(MessageReactionORM).filter(MessageReactionORM.message_id == message.id).all()]
    result["comments"] = [{"id": row.id, "content": row.content, "created_at": row.created_at, "deleted_at": row.deleted_at, "user": {"id": row.user.id, "username": row.user.username, "display_name": row.user.display_name}} for row in db.query(MessageCommentORM).filter(MessageCommentORM.message_id == message.id).all()]
    return result


@router.get("/files")
def list_files(user_id: Optional[str] = None, mime_prefix: str = "", date_from: Optional[int] = None, date_to: Optional[int] = None, min_size: Optional[int] = None, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size); query = db.query(FileORM)
    if user_id: query = query.filter(FileORM.user_id == user_id)
    if mime_prefix: query = query.filter(FileORM.mime_type.ilike(f"{mime_prefix[:80]}%"))
    if date_from is not None: query = query.filter(FileORM.created_at >= date_from)
    if date_to is not None: query = query.filter(FileORM.created_at <= date_to)
    if min_size is not None: query = query.filter(FileORM.file_size >= min_size)
    total = query.count(); rows = query.order_by(FileORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page_result([_file_summary(row) for row in rows], page, page_size, total)


@router.get("/storage/top-users")
def storage_top_users(limit: int = 20, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 100)
    rows = db.query(UserORM.id, UserORM.username, UserORM.display_name, func.count(FileORM.id), func.coalesce(func.sum(FileORM.file_size), 0)).outerjoin(FileORM, FileORM.user_id == UserORM.id).group_by(UserORM.id, UserORM.username, UserORM.display_name).order_by(func.coalesce(func.sum(FileORM.file_size), 0).desc()).limit(limit).all()
    return {"items": [{"id": row[0], "username": row[1], "display_name": row[2], "files_count": row[3], "bytes": int(row[4] or 0)} for row in rows]}


@router.get("/storage")
def storage_overview(db: Session = Depends(get_db)):
    now = int(time.time())
    rows = db.query(FileORM.mime_type, func.count(FileORM.id), func.coalesce(func.sum(FileORM.file_size), 0)).group_by(FileORM.mime_type).all()
    categories = {"images": {"files": 0, "bytes": 0}, "voice": {"files": 0, "bytes": 0}, "video": {"files": 0, "bytes": 0}, "other": {"files": 0, "bytes": 0}}
    for mime, count, size in rows:
        key = "images" if (mime or "").startswith("image/") else "voice" if (mime or "").startswith("audio/") else "video" if (mime or "").startswith("video/") else "other"
        categories[key]["files"] += int(count or 0); categories[key]["bytes"] += int(size or 0)
    usage = shutil.disk_usage(os.getenv("UPLOAD_ROOT", "/app/uploads"))
    uploads = {label: {"files": int(db.query(func.count(FileORM.id)).filter(FileORM.created_at >= since).scalar() or 0), "bytes": int(db.query(func.coalesce(func.sum(FileORM.file_size), 0)).filter(FileORM.created_at >= since).scalar() or 0)} for label, since in {"today": now - 86400, "7d": now - 7 * 86400, "30d": now - 30 * 86400}.items()}
    chats = db.query(ChatORM.id, ChatORM.name, ChatORM.chat_type, func.count(FileORM.id), func.coalesce(func.sum(FileORM.file_size), 0)).join(FileORM, FileORM.chat_id == ChatORM.id).group_by(ChatORM.id, ChatORM.name, ChatORM.chat_type).order_by(func.coalesce(func.sum(FileORM.file_size), 0).desc()).limit(20).all()
    threshold = int(os.getenv("UPLOAD_DISK_USAGE_THRESHOLD_PERCENT", "90"))
    return {"categories": categories, "total": {"files": sum(value["files"] for value in categories.values()), "bytes": sum(value["bytes"] for value in categories.values())}, "uploads": uploads, "disk": {"total": usage.total, "free": usage.free, "used": usage.used, "percent": round(usage.used * 100 / usage.total, 1), "reject_threshold_percent": threshold}, "top_chats": [{"id": row[0], "name": row[1], "type": row[2], "files_count": int(row[3]), "bytes": int(row[4])} for row in chats]}


@router.get("/security")
def security_overview():
    """Only current Redis limiter keys; no raw IPs/subjects and no fake history."""
    limits = [LOGIN_IP, REGISTER_IP_BURST, REGISTER_IP_HOUR, MESSAGE_BURST, MESSAGE_SUSTAINED, REACTION_EVENTS, COMMENT_EVENTS, INVITE_CREATE, WS_EVENTS]
    active = {limit.name: 0 for limit in limits}
    try:
        for key in redis_cache.redis_client.scan_iter("queenchat:rl:*"):
            name = key.decode() if isinstance(key, bytes) else str(key)
            for limit in limits:
                if name.startswith(f"queenchat:rl:{limit.name}:"):
                    active[limit.name] += 1; break
        redis_healthy = True
    except Exception:
        redis_healthy = False
    return {"source": "current Redis fixed-window keys; counters expire with their policy window", "redis_healthy": redis_healthy, "active_subject_windows": active, "policies": [{"name": limit.name, "maximum": limit.maximum, "window_seconds": limit.window_seconds} for limit in limits]}


@router.get("/invites")
def list_invites(status: Optional[Literal["active", "accepted", "expired", "revoked"]] = None, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size); now = int(time.time())
    # A SQL UNION keeps paging and filtering in PostgreSQL rather than loading
    # every invite into the API process.  Tokens/token hashes are intentionally
    # not selected.
    def invite_status(model):
        return case(
            (model.revoked_at.is_not(None), literal("revoked")),
            (model.accepted_at.is_not(None), literal("accepted")),
            (model.expires_at <= now, literal("expired")),
            else_=literal("active"),
        )

    chat_invites = db.query(
        PrivateChatInviteORM.id.label("id"), literal("private_chat").label("kind"),
        PrivateChatInviteORM.creator_user_id.label("creator_user_id"),
        literal(None).label("recipient_user_id"), literal(None).label("chat_id"),
        PrivateChatInviteORM.created_at.label("created_at"),
        PrivateChatInviteORM.expires_at.label("expires_at"), PrivateChatInviteORM.accepted_at.label("accepted_at"),
        PrivateChatInviteORM.accepted_by_user_id.label("accepted_by_user_id"),
        PrivateChatInviteORM.revoked_at.label("revoked_at"), invite_status(PrivateChatInviteORM).label("status"),
    )
    space_invites = db.query(
        PrivateSpaceInviteORM.id.label("id"), literal("space").label("kind"),
        PrivateSpaceInviteORM.creator_user_id.label("creator_user_id"), PrivateSpaceInviteORM.recipient_user_id.label("recipient_user_id"),
        PrivateSpaceInviteORM.chat_id.label("chat_id"), PrivateSpaceInviteORM.created_at.label("created_at"),
        PrivateSpaceInviteORM.expires_at.label("expires_at"), PrivateSpaceInviteORM.accepted_at.label("accepted_at"),
        PrivateSpaceInviteORM.accepted_by_user_id.label("accepted_by_user_id"),
        PrivateSpaceInviteORM.revoked_at.label("revoked_at"), invite_status(PrivateSpaceInviteORM).label("status"),
    )
    combined = union_all(chat_invites.statement, space_invites.statement).subquery()
    query = db.query(combined)
    if status: query = query.filter(combined.c.status == status)
    total = query.count()
    rows = query.order_by(combined.c.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page_result([dict(row._mapping) for row in rows], page, page_size, total)


@router.get("/spaces")
def list_spaces(status: Optional[Literal["active", "pending"]] = None, page: int = 1, page_size: int = 30, db: Session = Depends(get_db)):
    page, page_size = _page(page, page_size); query = db.query(PrivateSpaceSettingsORM)
    if status: query = query.filter(PrivateSpaceSettingsORM.status == status)
    total = query.count(); rows = query.order_by(PrivateSpaceSettingsORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return _page_result([{"chat_id": row.chat_id, "status": row.status, "title": row.title, "created_at": row.created_at, "updated_at": row.updated_at, "saved_count": db.query(func.count(SpaceMemoryORM.id)).filter(SpaceMemoryORM.chat_id == row.chat_id).scalar() or 0, "dates_count": db.query(func.count(SpaceDateORM.id)).filter(SpaceDateORM.chat_id == row.chat_id).scalar() or 0, "notes_count": db.query(func.count(SpaceNoteORM.id)).filter(SpaceNoteORM.chat_id == row.chat_id).scalar() or 0} for row in rows], page, page_size, total)


@router.get("/realtime")
def realtime_state():
    users = set(manager.global_connections)
    for by_user in manager.active_connections.values(): users.update(by_user)
    return {"websocket_users": len(users), "websocket_connections": sum(len(sockets) for sockets in manager.global_connections.values()) + sum(len(sockets) for by_user in manager.active_connections.values() for sockets in by_user.values()), "users": [{"user_id": user_id, "sockets": manager.connection_count(user_id)} for user_id in sorted(users)]}


@router.get("/search")
def admin_search(q: str, limit: int = 10, db: Session = Depends(get_db)):
    term = q.strip()
    if len(term) < 2: return {"users": [], "chats": [], "messages": []}
    limit = min(max(limit, 1), 30); like = f"%{term[:100]}%"
    users = db.query(UserORM).filter(or_(UserORM.id.ilike(like), UserORM.username.ilike(like), UserORM.display_name.ilike(like), UserORM.phone.ilike(like), UserORM.email.ilike(like))).limit(limit).all()
    chats = db.query(ChatORM).filter(or_(ChatORM.id.ilike(like), ChatORM.name.ilike(like))).limit(limit).all()
    # Deliberately no full-content message search in type-ahead.
    messages = db.query(MessageORM).filter(MessageORM.id.ilike(like)).limit(limit).all()
    return {"users": [_user_summary(db, row) for row in users], "chats": [_chat_summary(db, row) for row in chats], "messages": [{"id": row.id, "chat_id": row.chat_id, "created_at": row.created_at} for row in messages]}


@router.post("/invites/{kind}/{invite_id}/revoke")
def revoke_invite(kind: Literal["private_chat", "space"], invite_id: str, admin: UserORM = Depends(_admin_mutation), db: Session = Depends(get_db)):
    model = PrivateChatInviteORM if kind == "private_chat" else PrivateSpaceInviteORM
    invite = db.get(model, invite_id)
    if not invite: raise HTTPException(404, "Invitation not found")
    if invite.accepted_at or invite.revoked_at or invite.expires_at <= int(time.time()): raise HTTPException(409, "Only active invitations can be revoked")
    invite.revoked_at = int(time.time()); _audit(db, admin, "INVITE_REVOKE", "invite", invite.id, {"kind": kind}); db.commit()
    return {"status": "revoked", "id": invite.id}


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

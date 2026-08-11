"""Small Redis-backed fixed-window limiter used by public mutation endpoints."""
import hashlib
import logging
import os
import time
from dataclasses import dataclass

from fastapi import HTTPException

from app.core.redis import redis_client

logger = logging.getLogger(__name__)


def safe_key(value: str) -> str:
    """Never put phones, tokens, or raw addresses in Redis key names."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def client_ip(request) -> str:
    # Caddy overwrites X-Forwarded-For before proxying.  Do not accept it when
    # the app is reached directly (the compose bridge is the only trusted hop).
    host = request.client.host if request.client else "unknown"
    if host.startswith(("172.", "127.", "::1")):
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return host


@dataclass(frozen=True)
class Limit:
    name: str
    maximum: int
    window_seconds: int


def hit(limit: Limit, subject: str) -> int:
    # Existing isolated tests do not run Redis; production always uses Redis.
    if os.getenv("TESTING", "false").lower() == "true" and os.getenv("RATE_LIMIT_TESTING", "false").lower() != "true":
        return 0
    key = f"queenchat:rl:{limit.name}:{safe_key(subject)}"
    try:
        # INCR and EXPIRE are serialized by Redis; expiry is only set for a new key.
        count = int(redis_client.incr(key))
        if count == 1:
            redis_client.expire(key, limit.window_seconds)
        remaining = int(redis_client.ttl(key))
        if count > limit.maximum:
            logger.warning("%s subject_hash=%s", limit.name.upper() + "_RATE_LIMIT", safe_key(subject))
            raise HTTPException(429, detail="Too many requests. Please try again later.", headers={"Retry-After": str(max(1, remaining))})
        return count
    except HTTPException:
        raise
    except Exception:
        # Availability of auth must not depend on Redis during a transient outage.
        logger.exception("rate limiter unavailable policy=%s", limit.name)
        return 0


def clear(limit: Limit, subject: str) -> None:
    try:
        redis_client.delete(f"queenchat:rl:{limit.name}:{safe_key(subject)}")
    except Exception:
        logger.exception("rate limiter clear failed policy=%s", limit.name)


def count(limit: Limit, subject: str) -> int:
    if os.getenv("TESTING", "false").lower() == "true" and os.getenv("RATE_LIMIT_TESTING", "false").lower() != "true":
        return 0
    try:
        value = redis_client.get(f"queenchat:rl:{limit.name}:{safe_key(subject)}")
        return int(value or 0)
    except Exception:
        return 0


LOGIN_IP = Limit("login-ip", 10, 300)
LOGIN_ACCOUNT_FAILURE = Limit("login-account-failure", 5, 300)
LOGIN_CHALLENGE = Limit("login-challenge", 3, 300)
REGISTER_IP_HOUR = Limit("register-ip-hour", 5, 3600)
REGISTER_IP_BURST = Limit("register-ip-burst", 2, 60)
MESSAGE_BURST = Limit("message-burst", 20, 10)
MESSAGE_SUSTAINED = Limit("message-sustained", 120, 60)
INVITE_CREATE = Limit("invite-create", 10, 3600)
WS_EVENTS = Limit("ws-events", 60, 10)

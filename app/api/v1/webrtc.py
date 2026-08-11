"""Authenticated, short-lived ICE server configuration for WebRTC clients."""

import base64
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import UserORM
from app.core.dependency import get_current_user


router = APIRouter()

DEFAULT_TURN_HOST = "turn.queenchat.ru"
DEFAULT_TURN_TTL_SECONDS = 3600


def _turn_rest_credential(username: str, shared_secret: str) -> str:
    """Generate the HMAC-SHA1/Base64 credential required by coturn REST auth."""
    digest = hmac.new(shared_secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


@router.get("/ice-servers")
def get_ice_servers(current_user: UserORM = Depends(get_current_user)):
    shared_secret = os.getenv("TURN_SHARED_SECRET")
    if not shared_secret:
        # Do not fall back to a predictable secret or expose configuration state.
        raise HTTPException(status_code=503, detail="WebRTC relay is temporarily unavailable")

    try:
        ttl_seconds = int(os.getenv("TURN_CREDENTIAL_TTL_SECONDS", str(DEFAULT_TURN_TTL_SECONDS)))
    except ValueError:
        ttl_seconds = DEFAULT_TURN_TTL_SECONDS
    ttl_seconds = min(max(ttl_seconds, 300), 86_400)

    host = os.getenv("TURN_HOST", DEFAULT_TURN_HOST).strip() or DEFAULT_TURN_HOST
    username = f"{int(time.time()) + ttl_seconds}:{current_user.id}"

    return {
        "iceServers": [
            {"urls": [f"stun:{host}:3478"]},
            {
                "urls": [
                    f"turn:{host}:3478?transport=udp",
                    f"turn:{host}:3478?transport=tcp",
                ],
                "username": username,
                "credential": _turn_rest_credential(username, shared_secret),
            },
        ]
    }

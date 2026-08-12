import logging
import json
import os
import re
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.v1 import auth
from app.api.v1 import chats
from app.api.v1 import notifications
from app.api.v1 import files
from app.api.v1 import webrtc
from app.api.v1 import admin
from app.api.v1 import spaces
from app.api.v1 import chat_invites
from app.core.database import lifespan
from app.core import firebase
from app.core.logging import configure_logging
from app.core.telemetry import (ANDROID_UPDATE_AVAILABLE, ANDROID_UPDATE_CHECKS,
    ANDROID_UPDATE_DOWNLOADS, ANDROID_UPDATE_VERIFY_FAILED, HTTP_DURATION,
    HTTP_IN_PROGRESS, HTTP_REQUESTS)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

configure_logging()
logger = logging.getLogger(__name__)

def _metric_route(path: str) -> str:
    """Avoid raw IDs in metrics before FastAPI has selected a route."""
    path = re.sub(r"/api/chats/[^/]+/messages/[^/]+", "/api/chats/{chat_id}/messages/{message_id}", path)
    path = re.sub(r"/api/chats/[^/]+", "/api/chats/{chat_id}", path)
    return path

app = FastAPI(
    title="QueenChat API",
    version="1.0.0",
    contact={"name": "Denis", "email": "k1ndenis.dev@gmail.com"},
    lifespan=lifespan,
    redirect_slashes=False
)

@app.middleware("http")
async def observability_and_admin_headers(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    started = time.perf_counter()
    # Use the matched route after dispatch; never export raw paths or query strings.
    route = request.scope.get("route")
    normalized_route = getattr(route, "path", None) or _metric_route(request.url.path)
    if normalized_route == "/metrics":
        return await call_next(request)
    in_progress = HTTP_IN_PROGRESS.labels(request.method, normalized_route)
    in_progress.inc()
    try:
        response = await call_next(request)
    except Exception:
        duration = time.perf_counter() - started
        HTTP_REQUESTS.labels(request.method, normalized_route, "500").inc()
        HTTP_DURATION.labels(request.method, normalized_route).observe(duration)
        logger.exception("request_failed", extra={"event": "request_failed", "request_id": request_id, "route": normalized_route, "method": request.method, "status_code": 500, "duration_ms": round(duration * 1000, 2)})
        raise
    finally:
        in_progress.dec()
    duration = time.perf_counter() - started
    # Router population happens during dispatch, so re-read it here.
    normalized_route = getattr(request.scope.get("route"), "path", normalized_route)
    HTTP_REQUESTS.labels(request.method, normalized_route, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, normalized_route).observe(duration)
    response.headers["X-Request-ID"] = request_id
    logger.info("request_completed", extra={"event": "request_completed", "request_id": request_id, "route": normalized_route, "method": request.method, "status_code": response.status_code, "duration_ms": round(duration * 1000, 2)})
    if request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://queenchat.ru",
        "https://www.queenchat.ru"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

uploads_path = files.UPLOAD_ROOT
if uploads_path.exists():
    app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chats.router, prefix="/api/chats", tags=["chats"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(webrtc.router, prefix="/api/webrtc", tags=["webrtc"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(spaces.router, prefix="/api/spaces", tags=["spaces"])
app.include_router(chat_invites.router, prefix="/api/chats/invites", tags=["chat invites"])

ANDROID_RELEASE_CONFIG = Path(os.getenv("ANDROID_RELEASE_CONFIG", "/app/releases/android_release.json"))
ANDROID_RELEASE_FIELDS = {
    "platform", "version_code", "version_name", "minimum_version_code", "mandatory",
    "apk_url", "sha256", "size_bytes", "changelog", "published_at",
}

def load_android_release() -> dict:
    try:
        raw = json.loads(ANDROID_RELEASE_CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Android release is not published") from exc
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("android_release_config_invalid", extra={"event": "android_release_config_invalid"})
        raise HTTPException(status_code=503, detail="Android release metadata is unavailable") from exc
    required = {"version_code", "version_name", "minimum_version_code", "mandatory", "apk_url", "sha256", "size_bytes", "changelog"}
    if not isinstance(raw, dict) or not required.issubset(raw) or raw.get("platform", "android") != "android":
        raise HTTPException(status_code=503, detail="Android release metadata is unavailable")
    if not isinstance(raw["version_code"], int) or not isinstance(raw["minimum_version_code"], int) or raw["version_code"] < 1 or raw["minimum_version_code"] < 1:
        raise HTTPException(status_code=503, detail="Android release metadata is unavailable")
    if not isinstance(raw["apk_url"], str) or not raw["apk_url"].startswith("https://queenchat.ru/downloads/"):
        raise HTTPException(status_code=503, detail="Android release metadata is unavailable")
    if not isinstance(raw["sha256"], str) or not re.fullmatch(r"[a-fA-F0-9]{64}", raw["sha256"]):
        raise HTTPException(status_code=503, detail="Android release metadata is unavailable")
    if not isinstance(raw["changelog"], list) or not all(isinstance(item, str) and len(item) <= 240 for item in raw["changelog"]):
        raise HTTPException(status_code=503, detail="Android release metadata is unavailable")
    return {field: raw[field] for field in ANDROID_RELEASE_FIELDS if field in raw} | {"platform": "android"}

@app.get("/api/app/android/version", include_in_schema=False)
def android_version():
    try:
        release = load_android_release()
    except HTTPException as exc:
        ANDROID_UPDATE_CHECKS.labels("unavailable").inc()
        raise exc
    ANDROID_UPDATE_CHECKS.labels("success").inc()
    ANDROID_UPDATE_AVAILABLE.inc()
    return JSONResponse(release, headers={"Cache-Control": "no-cache, max-age=0"})

@app.post("/api/app/android/update-events", include_in_schema=False)
async def android_update_event(request: Request):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid update event")
    event = body.get("event") if isinstance(body, dict) else None
    if event == "download_success": ANDROID_UPDATE_DOWNLOADS.labels("success").inc()
    elif event == "download_failed": ANDROID_UPDATE_DOWNLOADS.labels("failed").inc()
    elif event == "verify_failed": ANDROID_UPDATE_VERIFY_FAILED.inc()
    else: raise HTTPException(status_code=400, detail="Invalid update event")
    return Response(status_code=204)

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/")
def read_root():
    return {"message": "Welcome to the QueenChat API"}

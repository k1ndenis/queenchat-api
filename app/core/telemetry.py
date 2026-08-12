"""Low-cardinality Prometheus metrics for QueenChat.

Do not add identifiers, addresses, tokens, or request IDs as labels here.
"""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "normalized_route", "status_code"])
HTTP_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "normalized_route"], buckets=(.01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10))
HTTP_IN_PROGRESS = Gauge("http_requests_in_progress", "HTTP requests in progress", ["method", "normalized_route"])

USERS_REGISTERED = Counter("queenchat_users_registered_total", "Registered users")
LOGIN_SUCCESS = Counter("queenchat_login_success_total", "Successful logins")
LOGIN_FAILED = Counter("queenchat_login_failed_total", "Failed logins")
MESSAGES_SENT = Counter("queenchat_messages_sent_total", "Messages sent")
REACTIONS = Counter("queenchat_reactions_total", "Reactions set")
COMMENTS = Counter("queenchat_comments_total", "Comments created")
UPLOADS = Counter("queenchat_uploads_total", "Completed uploads", ["type"])
UPLOAD_BYTES = Counter("queenchat_upload_bytes_total", "Uploaded bytes", ["type"])
INVITES_CREATED = Counter("queenchat_invites_created_total", "Created invites", ["type"])
INVITES_ACCEPTED = Counter("queenchat_invites_accepted_total", "Accepted invites", ["type"])
RATE_LIMIT_HITS = Counter("queenchat_rate_limit_hits_total", "Rate-limit rejections", ["policy"])

WS_CONNECTIONS = Gauge("queenchat_websocket_connections", "Active WebSocket connections", ["type"])
# prometheus_client strips a trailing ``_total`` from Counter names internally.
# Keep this distinct from the active-connections gauge above.
WS_CONNECTIONS_TOTAL = Counter("queenchat_websocket_connections_opened_total", "WebSocket connections opened", ["type"])
WS_DISCONNECTS_TOTAL = Counter("queenchat_websocket_disconnects_total", "WebSocket disconnects", ["type"])
WS_EVENTS_TOTAL = Counter("queenchat_websocket_events_total", "WebSocket events", ["type"])
WS_RATE_LIMIT_TOTAL = Counter("queenchat_websocket_rate_limit_total", "WebSocket rate limit rejections", ["type"])

CALLS_STARTED = Counter("queenchat_calls_started_total", "WebRTC offer signals")
CALL_SIGNALS = Counter("queenchat_call_signals_total", "WebRTC signals", ["signal_type"])
ICE_CREDENTIALS = Counter("queenchat_ice_credentials_issued_total", "TURN ICE credentials issued")

ANDROID_UPDATE_CHECKS = Counter("queenchat_android_update_check_total", "Android update checks", ["result"])
ANDROID_UPDATE_AVAILABLE = Counter("queenchat_android_update_available_total", "Android update availability responses")
ANDROID_UPDATE_DOWNLOADS = Counter("queenchat_android_update_download_total", "Android update download outcomes", ["result"])
ANDROID_UPDATE_VERIFY_FAILED = Counter("queenchat_android_update_verify_failed_total", "Android APK verification failures")

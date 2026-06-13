import firebase_admin
from firebase_admin import credentials, messaging
import os

cred_path = os.getenv("FIREBASE_SERVICE_ACCOUNT")

if os.getenv("TESTING", "false").lower() == "true":
    print("⚠️ Firebase disabled in test mode")
    firebase_admin.initialize_app()
elif cred_path and os.path.exists(cred_path):
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    print("✅ Firebase initialized with certificate")
else:
    print("⚠️ Firebase initialized without certificate (development mode)")
    firebase_admin.initialize_app()
import requests
import os

class CaptchaService:
    def __init__(self):
        self.secret_key = os.getenv("TURNSTILE_SECRET_KEY", "")
        self.enabled = os.getenv("TURNSTILE_ENABLED", "false").lower() == "true"
    
    def verify(self, token: str) -> bool:
        if not self.enabled:
            return True
        if not token or not self.secret_key:
            return False
        
        try:
            response = requests.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data={
                    "secret": self.secret_key,
                    "response": token
                },
                timeout=5
            )
            result = response.json()
            return result.get("success", False)
        except Exception as e:
            print(f"Captcha verification error: {e}")
            return False

captcha_service = CaptchaService()

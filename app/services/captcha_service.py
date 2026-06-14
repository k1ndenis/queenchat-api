import requests
import os

class CaptchaService:
    def __init__(self):
        self.secret_key = os.getenv("RECAPTCHA_SECRET_KEY", "")
    
    def verify(self, token: str) -> bool:
        if not token or not self.secret_key:
            return False
        
        try:
            response = requests.post(
                "https://www.google.com/recaptcha/api/siteverify",
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
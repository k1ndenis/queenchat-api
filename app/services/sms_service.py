import requests
import os

class SMSService:
    def __init__(self, api_id: str = None):
        self.api_id = api_id or os.getenv("SMS_API_ID", "")
    
    def send_code(self, phone: str, code: str) -> dict:
        url = "https://sms.ru/sms/send"
        
        response = requests.post(
            url,
            data={
                "api_id": self.api_id,
                "to": phone,
                "msg": f"Ваш код подтверждения: {code}",
                "json": 1
            }
        )
        
        result = response.json()
        
        if result.get("status") == "OK":
            sms_result = result.get("sms", {}).get(phone, {})
            if sms_result.get("status") == "OK":
                return {"status": "OK", "sms_id": sms_result.get("sms_id")}
            else:
                error_code = sms_result.get("status_code", "unknown")
                error_text = sms_result.get("status_text", "Unknown error")
                print(f"❌ SMS error for {phone}: code={error_code}, text={error_text}")
                return {"status": "ERROR", "error_code": error_code, "error_text": error_text}
        else:
            print(f"❌ SMS API error: {result}")
            return {"status": "ERROR", "error": result}
    
    def send_code_test_mode(self, phone: str, code: str) -> dict:
        """Тестовый режим - только лог"""
        print(f"\n📱 ===== SMS TEST MODE =====")
        print(f"Phone: {phone}")
        print(f"Code: {code}")
        print(f"===========================\n")
        return {"status": "OK", "test_mode": True}
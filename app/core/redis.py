import os
import sys

TESTING = os.getenv("TESTING", "false").lower() == "true" or "pytest" in sys.modules

if TESTING:
    print("⚠️ Redis is DISABLED in test mode")
    
    class MockRedis:
        def ping(self): 
            return True
        
        def set(self, *args, **kwargs): 
            return True
        
        def setex(self, *args, **kwargs):
            return True
        
        def get(self, *args, **kwargs): 
            return None
        
        def delete(self, *args, **kwargs): 
            return 1
        
        def scan_iter(self, *args, **kwargs): 
            return iter([])
        
        def exists(self, *args, **kwargs):
            return 0
        
        def close(self): 
            pass
    
    class RedisCacheBackend:
        def __init__(self, redis_url: str = None, ttl_seconds: int = 60):
            self.ttl_seconds = ttl_seconds
            self.redis_client = MockRedis()
        
        def set(self, key: str, value: dict, ttl: int = None):
            return True
        
        def setex(self, key: str, time: int, value: str):
            return True
            
        def get(self, key: str) -> dict | None:
            return None
            
        def delete(self, key: str):
            return True
            
        def delete_pattern(self, pattern: str):
            return True
            
        def exists(self, key: str) -> bool:
            return False
            
        def close(self):
            pass
    
    redis_cache = RedisCacheBackend()
    redis_client = redis_cache
    
else:
    from redis import Redis
    import json
    from dotenv import load_dotenv
    
    load_dotenv()
    REDIS_URL = os.getenv("REDIS_URL")
    
    redis_client = Redis.from_url(REDIS_URL)
    
    class RedisCacheBackend:
        def __init__(self, redis_url: str = REDIS_URL, ttl_seconds: int = 60):
            self.redis_client = Redis.from_url(redis_url)
            self.ttl_seconds = ttl_seconds
    
        def set(self, key: str, value: dict, ttl: int = None):
            ttl_value = ttl if ttl is not None else self.ttl_seconds
            return self.redis_client.set(key, json.dumps(value), ex=ttl_value)
        
        def setex(self, key: str, time: int, value: str):
            """Set string value with expiration in seconds"""
            return self.redis_client.setex(key, time, value)
            
        def get(self, key: str) -> dict | None:
            value = self.redis_client.get(key)
            if value:
                # Пытаемся распарсить как JSON, если не получается - возвращаем как строку
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    return value if value else None
            return None
        
        def get_raw(self, key: str) -> str | None:
            value = self.redis_client.get(key)
            return value.decode() if value else None
        
        def delete(self, key: str):
            return self.redis_client.delete(key)
        
        def delete_pattern(self, pattern: str):
            count = 0
            for key in self.redis_client.scan_iter(pattern):
                self.redis_client.delete(key)
                count += 1
            return count
        
        def exists(self, key: str) -> bool:
            return self.redis_client.exists(key) > 0
        
        def close(self):
            self.redis_client.close()
    
    redis_cache = RedisCacheBackend()
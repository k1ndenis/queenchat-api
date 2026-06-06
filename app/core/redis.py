import os
import sys

TESTING = os.getenv("TESTING", "false").lower() == "true" or "pytest" in sys.modules

if TESTING:
    print("⚠️ Redis is DISABLED in test mode")
    
    class MockRedis:
        def ping(self): return True
        def set(self, *args, **kwargs): return True
        def get(self, *args, **kwargs): return None
        def delete(self, *args, **kwargs): return True
        def scan_iter(self, *args, **kwargs): return []
        def close(self): pass
    
    class RedisCacheBackend:
        def __init__(self, redis_url: str = None, ttl_seconds: int = 60):
            self.ttl_seconds = ttl_seconds
            self.redis_client = MockRedis()
        
        def set(self, key: str, value: dict): pass
        def get(self, key: str) -> dict | None: return None
        def delete(self, key: str): pass
        def delete_pattern(self, pattern: str): pass
        def close(self): pass
    
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
    
        def set(self, key: str, value: dict):
            self.redis_client.set(key, json.dumps(value), ex=self.ttl_seconds)
            
        def get(self, key: str) -> dict | None:
            value = self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        
        def delete(self, key: str):
            self.redis_client.delete(key)
        
        def delete_pattern(self, pattern: str):
            for key in self.redis_client.scan_iter(pattern):
                self.redis_client.delete(key)
    
    redis_cache = RedisCacheBackend()
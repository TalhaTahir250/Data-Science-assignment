import time
import logging
from collections import defaultdict
from fastapi import HTTPException, Header

logger = logging.getLogger("PakVerify.Security")

class TokenBucketLimiter:
    def __init__(self, rate_per_minute: int = 20, capacity: int = 20):
        """
        Implements an in-memory Token Bucket rate limiter.
        rate_per_minute: Number of tokens added to the bucket every minute.
        capacity: Maximum number of tokens a bucket can hold.
        """
        self.rate_per_minute = rate_per_minute
        self.capacity = capacity
        self.fill_rate = rate_per_minute / 60.0  # Tokens per second
        self.buckets = defaultdict(lambda: {"tokens": capacity, "last_updated": time.time()})

    def is_rate_limited(self, identifier: str) -> bool:
        """
        Checks if a specific identifier (like an API Key) has exceeded its limit.
        Returns True if rate-limited, False if allowed.
        """
        now = time.time()
        bucket = self.buckets[identifier]
        
        # Calculate how many tokens accumulated since the last request
        elapsed = now - bucket["last_updated"]
        bucket["tokens"] = min(self.capacity, bucket["tokens"] + (elapsed * self.fill_rate))
        bucket["last_updated"] = now
        
        # Consume a token if available
        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return False
            
        return True

# Initialize a global limiter allowing a maximum of 20 scans per minute per client
scan_limiter = TokenBucketLimiter(rate_per_minute=20, capacity=20)

def RateLimitShield(x_api_key: str = Header(None)):
    """FastAPI Dependency that enforces rate limits on incoming routes."""
    if not x_api_key:
        return  # Let the auth checker handle missing keys
        
    if scan_limiter.is_rate_limited(x_api_key):
        logger.warning(f"Rate Limit Exceeded: Tenant [{x_api_key[:8]}...] blocked.")
        raise HTTPException(
            status_code=429, 
            detail="Too Many Requests. Your API key is restricted to 20 verifications per minute."
        )
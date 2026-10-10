"""In-memory token bucket rate limiter for API keys."""

import asyncio
import time
from typing import Dict, Tuple


class TokenBucket:
    """Token bucket algorithm implementation for an individual key."""

    def __init__(self, capacity: float, refill_rate_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate_per_sec)
        self.tokens = float(capacity)
        self.last_update = time.monotonic()

    def consume(self, cost: float = 1.0) -> Tuple[bool, int]:
        """Attempt to consume tokens.
        
        Returns:
            Tuple of (is_allowed: bool, remaining_tokens: int)
        """
        now = time.monotonic()
        elapsed = now - self.last_update
        self.last_update = now

        # Add newly accrued tokens up to capacity
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))

        if self.tokens >= cost:
            self.tokens -= cost
            return True, int(self.tokens)
        else:
            return False, int(self.tokens)


class TokenBucketRateLimiter:
    """Thread-safe and async-safe in-memory rate limiter per key."""

    def __init__(self, rate_limit_per_minute: int = 1000) -> None:
        self.rate_limit_per_minute = rate_limit_per_minute
        self.refill_rate_per_sec = rate_limit_per_minute / 60.0
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str, cost: int = 1) -> Tuple[bool, int]:
        """Check rate limit for a given key.
        
        Args:
            key: Unique key identifier (e.g. key_hash or key_id)
            cost: Number of tokens requested
            
        Returns:
            Tuple of (allowed: bool, remaining_tokens: int)
        """
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self.rate_limit_per_minute,
                    refill_rate_per_sec=self.refill_rate_per_sec,
                )
                self._buckets[key] = bucket

            return bucket.consume(cost)

    def reset(self) -> None:
        """Reset all rate limiter buckets (used in test fixtures)."""
        self._buckets.clear()

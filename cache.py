"""
Telegram Bridge — Download URL Cache
In-memory cache for Telegram download URLs with TTL.
Telegram URLs are valid for ~1 hour; we cache for 50 min to stay safe.
"""

import time
import threading
from typing import Optional


class DownloadURLCache:
    """
    Thread-safe in-memory cache for file download URLs.
    Uses file_unique_id as key, with configurable TTL (default 50 min).
    """

    def __init__(self, ttl_seconds: int = 3000, max_size: int = 10000):
        self._cache: dict[str, tuple[str, float]] = {}  # key -> (url, expiry_time)
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[str]:
        """Get cached URL if not expired."""
        with self._lock:
            if key in self._cache:
                url, expiry = self._cache[key]
                if time.time() < expiry:
                    self._hits += 1
                    return url
                else:
                    # Expired — remove it
                    del self._cache[key]
            self._misses += 1
            return None

    def set(self, key: str, url: str) -> None:
        """Cache a URL with TTL."""
        with self._lock:
            # Evict oldest if at capacity
            if len(self._cache) >= self._max_size and key not in self._cache:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]

            self._cache[key] = (url, time.time() + self._ttl)

    def invalidate(self, key: str) -> bool:
        """Remove a specific key from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        """Clear all cached entries. Returns count of cleared entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def cleanup(self) -> int:
        """Remove all expired entries. Returns count removed."""
        with self._lock:
            now = time.time()
            expired = [k for k, (_, exp) in self._cache.items() if now >= exp]
            for k in expired:
                del self._cache[k]
            return len(expired)

    @property
    def stats(self) -> dict:
        """Cache statistics."""
        with self._lock:
            return {
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": self._hits / max(1, self._hits + self._misses),
            }


# Singleton instance
url_cache = DownloadURLCache()

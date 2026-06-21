"""
Telegram Bridge — Rate Limiting Middleware
Simple in-memory rate limiter using sliding window.
"""

import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiter using sliding window counter.
    Tracks requests per IP (or API key) per endpoint.
    """

    def __init__(self, app, requests_per_minute: int = 60, upload_rpm: int = 10):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.upload_rpm = upload_rpm
        # {key: [timestamp, ...]}
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._uploads: dict[str, list[float]] = defaultdict(list)

    def _get_key(self, request: Request) -> str:
        """Get rate limit key from API key header or client IP."""
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            return f"key:{api_key}"
        client = request.client
        if client:
            return f"ip:{client.host}"
        return "ip:unknown"

    def _clean(self, timestamps: list[float], window: float = 60.0):
        """Remove timestamps older than the window."""
        now = time.time()
        while timestamps and timestamps[0] < now - window:
            timestamps.pop(0)

    async def dispatch(self, request: Request, call_next):
        key = self._get_key(request)
        now = time.time()

        # Clean old entries
        self._clean(self._requests[key])
        self._clean(self._uploads[key])

        # Check upload rate limit
        if request.method == "POST" and "/upload" in request.url.path:
            if len(self._uploads[key]) >= self.upload_rpm:
                origin = request.headers.get("origin", "*")
                return JSONResponse(
                    status_code=429,
                    content={
                        "success": False,
                        "error": {
                            "code": "RATE_LIMITED",
                            "message": f"Upload rate limit exceeded. Max {self.upload_rpm} uploads/minute.",
                            "retry_after": 60,
                        }
                    },
                    headers={
                        "Retry-After": "60",
                        "Access-Control-Allow-Origin": origin,
                        "Access-Control-Allow-Credentials": "true",
                    },
                )
            self._uploads[key].append(now)

        # Check general rate limit
        if len(self._requests[key]) >= self.requests_per_minute:
            origin = request.headers.get("origin", "*")
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": f"Rate limit exceeded. Max {self.requests_per_minute} requests/minute.",
                        "retry_after": 60,
                    }
                },
                headers={
                    "Retry-After": "60",
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Credentials": "true",
                },
            )
        self._requests[key].append(now)

        response = await call_next(request)
        return response

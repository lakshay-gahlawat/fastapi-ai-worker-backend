import time
from collections.abc import Callable, Sequence
from typing import Optional

import redis.asyncio as redis
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class AsyncRateLimitMiddleware(BaseHTTPMiddleware):
    """Redis-backed sliding-window rate limiter.

    By default only POST (and other mutating methods) are counted so that
    status polling and static asset GETs are not throttled.
    """

    def __init__(
        self,
        app,
        redis_url: str = "redis://localhost:6379/0",
        max_requests: int = 100,
        window_seconds: int = 60,
        methods: Optional[Sequence[str]] = None,
        skip_path_prefixes: Optional[Sequence[str]] = None,
    ):
        super().__init__(app)
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.methods = {m.upper() for m in (methods or ("POST", "PUT", "PATCH", "DELETE"))}
        self.skip_path_prefixes = tuple(
            skip_path_prefixes
            or ("/docs", "/redoc", "/openapi.json", "/health", "/static")
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if request.method.upper() not in self.methods:
            return await call_next(request)
        if any(path == prefix or path.startswith(prefix.rstrip("/") + "/") or path.startswith(prefix)
               for prefix in self.skip_path_prefixes):
            return await call_next(request)

        client_ip = request.client.host if request.client else "127.0.0.1"
        current_time = int(time.time())
        key = f"ratelimit:{client_ip}:{current_time // self.window_seconds}"

        try:
            requests_count = await self.redis_client.incr(key)
            if requests_count == 1:
                await self.redis_client.expire(key, self.window_seconds)

            if requests_count > self.max_requests:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Rate limit exceeded. Try again later."},
                )
        except Exception:
            # Fail open if Redis is temporarily unreachable.
            pass

        return await call_next(request)


class RateLimiter(AsyncRateLimitMiddleware):
    """Public name used by apps; accepts both package and app keyword aliases."""

    def __init__(
        self,
        app,
        redis_url: str = "redis://localhost:6379/0",
        max_requests: int = 100,
        window_seconds: int = 60,
        requests_limit: Optional[int] = None,
        time_window: Optional[int] = None,
        methods: Optional[Sequence[str]] = None,
        skip_path_prefixes: Optional[Sequence[str]] = None,
    ):
        super().__init__(
            app,
            redis_url=redis_url,
            max_requests=requests_limit if requests_limit is not None else max_requests,
            window_seconds=time_window if time_window is not None else window_seconds,
            methods=methods,
            skip_path_prefixes=skip_path_prefixes,
        )

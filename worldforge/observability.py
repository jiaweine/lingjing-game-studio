from __future__ import annotations

import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from threading import Lock

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("worldforge.http")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self' ws: wss:; media-src 'self' blob:;",
        )
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, log_requests=True):
        super().__init__(app)
        self.log_requests = log_requests

    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if self.log_requests:
            logger.info(
                json.dumps(
                    {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round(
                            (time.perf_counter() - started) * 1000, 2
                        ),
                    },
                    ensure_ascii=False,
                )
            )
        return response


class SlidingWindowRateLimiter:
    """In-process sliding-window limiter with bounded key cardinality.

    The limiter is intentionally best-effort process-local protection. A hard key cap
    prevents untrusted high-cardinality identities/IPs from turning the bookkeeping
    dictionary itself into an unbounded memory sink.
    """

    def __init__(
        self,
        limit_per_minute: int,
        *,
        max_keys: int = 10_000,
        sweep_interval_seconds: float = 30.0,
    ):
        self.limit = max(1, int(limit_per_minute))
        self.max_keys = max(128, int(max_keys))
        self.sweep_interval_seconds = max(1.0, float(sweep_interval_seconds))
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()
        self._last_sweep = 0.0

    @staticmethod
    def _prune(queue: deque[float], cutoff: float) -> None:
        while queue and queue[0] < cutoff:
            queue.popleft()

    def _sweep_stale(self, now: float) -> None:
        if now - self._last_sweep < self.sweep_interval_seconds:
            return
        cutoff = now - 60
        stale = []
        for key, queue in self._hits.items():
            self._prune(queue, cutoff)
            if not queue:
                stale.append(key)
        for key in stale:
            self._hits.pop(key, None)
        self._last_sweep = now

    def check(self, key: str) -> None:
        now = time.time()
        cutoff = now - 60
        with self._lock:
            self._sweep_stale(now)
            queue = self._hits.get(key)
            if queue is None:
                while len(self._hits) >= self.max_keys:
                    self._hits.popitem(last=False)
                queue = deque()
                self._hits[key] = queue
            else:
                self._prune(queue, cutoff)
                self._hits.move_to_end(key)

            if len(queue) >= self.limit:
                retry_after = max(1, int(60 - (now - queue[0])))
                raise HTTPException(
                    429,
                    "请求过于频繁，请稍后再试",
                    headers={"Retry-After": str(retry_after)},
                )
            queue.append(now)

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._hits)

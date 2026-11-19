"""Authentication service initialization and per-process request limits."""
from collections import deque
import os
from threading import Lock
import time

from fastapi import HTTPException

from .service import AuthService


class RateLimiter:
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def check(self, key, limit):
        now = time.monotonic()
        with self.lock:
            for old_key in list(self.entries):
                queue = self.entries[old_key]
                while queue and queue[0] <= now - 60:
                    queue.popleft()
                if not queue:
                    del self.entries[old_key]
            queue = self.entries.setdefault(key, deque())
            if len(queue) >= limit:
                raise HTTPException(429, detail="rate_limited", headers={"Retry-After": "60"})
            queue.append(now)


def initialize_auth(app, database):
    app.state.auth = AuthService(database)
    app.state.auth_limiter = RateLimiter()
    app.state.auth_origins = {value.strip().rstrip("/") for value in
                              os.getenv("TCG_TRUSTED_ORIGINS", "").split(",") if value.strip()}



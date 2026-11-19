"""Request cancellation and per-account capacity for grading lookups."""
import logging
import socket
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# Cancellation: a lookup started with a request id can be stopped from another request.
# Stopping closes the upstream HTTP client (an in-flight read fails at once) and, through
# ``LookupToken.finish``, guarantees a cancelled lookup is never saved.
_current_lookup: ContextVar['LookupToken | None'] = ContextVar('grading_lookup', default=None)


class LookupToken:
    def __init__(self, cancelled=False):
        self._lock = threading.Lock()
        self._closers = []
        self.cancelled, self.finished = cancelled, False

    def attach(self, close):
        with self._lock:
            if not self.cancelled:
                self._closers.append(close)
                return
        close()

    def cancel(self):
        """True when the result will not be used; False once it was already kept."""
        with self._lock:
            if self.finished:
                return False
            self.cancelled = True
            closers, self._closers = self._closers, []
        for close in reversed(closers):
            try:
                close()
            except Exception:  # closing is best effort; the token already blocks the save
                logger.debug('closing a cancelled lookup failed', exc_info=True)
        return True

    def finish(self):
        """Claims the result for saving; False if the lookup was cancelled first."""
        with self._lock:
            if self.cancelled:
                return False
            self.finished = True
            return True


def cancellable_client(client):
    token = _current_lookup.get()
    if token is not None:
        token.attach(client.close)

        def trace(event, info):
            if event in ('connection.connect_tcp.complete', 'connection.start_tls.complete'):
                stream = info['return_value']
                connection = stream.get_extra_info('socket')
                if connection is not None:
                    # close() alone does not interrupt another thread's recv() on Linux.
                    token.attach(lambda: connection.shutdown(socket.SHUT_RDWR))

        def before_request(request):
            request.extensions['trace'] = trace

        client.event_hooks['request'].append(before_request)
    return client


def raise_if_cancelled():
    token = _current_lookup.get()
    if token is not None and token.cancelled:
        raise HTTPException(409, detail='grading_lookup_cancelled')


class LookupRegistry:
    """Lookups in flight by (user, request id); process-local like ``LookupSlots``.

    Outcomes are remembered for a few minutes so a cancel that arrives before its lookup
    starts still stops it, and one that arrives after the save can say so.
    """
    TTL = 300

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._active = {}
        self._outcomes = {}

    def _purge(self, now):
        self._outcomes = {key: value for key, value in self._outcomes.items() if now - value[1] < self.TTL}

    @contextmanager
    def track(self, user_id, request_id=None):
        key = (user_id, request_id)
        with self._lock:
            self._purge(self._clock())
            token = LookupToken(cancelled=request_id is not None and self._outcomes.get(key, ('',))[0] == 'cancelled')
            if request_id is not None:
                self._active[key] = token
        reset = _current_lookup.set(token)
        try:
            yield token
        finally:
            _current_lookup.reset(reset)
            if request_id is not None:
                with self._lock:
                    if self._active.get(key) is token:
                        del self._active[key]
                    self._outcomes[key] = ('finished' if token.finished else 'cancelled', self._clock())

    def cancel(self, user_id, request_id):
        """'cancelled' (nothing will be saved) or 'finished' (already saved)."""
        key = (user_id, request_id)
        with self._lock:
            self._purge(self._clock())
            token = self._active.get(key)
            if token is None:
                if self._outcomes.get(key, ('',))[0] == 'finished':
                    return 'finished'
                self._outcomes[key] = ('cancelled', self._clock())
                return 'cancelled'
        return 'cancelled' if token.cancel() else 'finished'


class LookupSlots:
    """Non-blocking caps on certificate lookups in flight.

    A PSA or CGC lookup can hold a worker thread for about 225 seconds, and sync routes
    share one threadpool. Process-local, like the auth rate limiter.
    """

    def __init__(self, per_user=3, total=8):
        self.per_user, self.total = per_user, total
        self._lock = threading.Lock()
        self._users = Counter()
        self._active = 0

    @contextmanager
    def hold(self, user_id):
        with self._lock:
            if self._active >= self.total or self._users[user_id] >= self.per_user:
                raise HTTPException(429, detail='grading_busy')
            self._active += 1
            self._users[user_id] += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
                self._users[user_id] -= 1
                if self._users[user_id] <= 0:
                    del self._users[user_id]

"""In-process rate limiting for the auth endpoints. Phase 5.7.

    Rate limiting on auth endpoints

A fixed-window counter held in process memory. Three honest limitations,
stated here rather than discovered later:

* **It is per-process.** Two API workers mean two counters and twice the
  allowance. NODE A runs a single API container, so today the limit is the
  limit; if the API is ever scaled out, this moves into Postgres.
* **It resets on restart.** An attacker who can restart the API has already
  won something bigger.
* **It is not the account lockout.** The lockout in :mod:`app.services.auth`
  protects one account against guessing; this protects the *server* against
  volume, including credential spraying across many accounts, which the
  per-account lockout cannot see.

Deliberately not Redis. The locked stack says one Postgres and no Redis, and
a table write per login attempt would be a worse trade than an approximate
in-memory counter for a fifty-user hospital deployment.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

# "Rate limiting on auth endpoints" with no number given. Twenty attempts per
# minute per address is far above a human typing a password and far below what
# a spray needs to be useful.
LOGIN_ATTEMPTS_PER_WINDOW = 20
WINDOW_SECONDS = 60.0


@dataclass
class _Window:
    started_at: float
    count: int = 0


@dataclass
class RateLimiter:
    limit: int = LOGIN_ATTEMPTS_PER_WINDOW
    window_seconds: float = WINDOW_SECONDS
    _windows: dict[str, _Window] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)`` and count the attempt."""
        moment = now if now is not None else time.monotonic()
        with self._lock:
            self._evict(moment)
            window = self._windows.get(key)
            if window is None or moment - window.started_at >= self.window_seconds:
                self._windows[key] = _Window(started_at=moment, count=1)
                return True, 0.0
            window.count += 1
            if window.count > self.limit:
                return False, self.window_seconds - (moment - window.started_at)
            return True, 0.0

    def _evict(self, moment: float) -> None:
        """Drop expired windows.

        Without this the dict is an unbounded memory leak keyed on
        attacker-supplied addresses — a slow one, but a real one on a process
        that runs for months.
        """
        if len(self._windows) < 1024:
            return
        stale = [
            key
            for key, win in self._windows.items()
            if moment - win.started_at >= self.window_seconds
        ]
        for key in stale:
            del self._windows[key]

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


login_limiter = RateLimiter()

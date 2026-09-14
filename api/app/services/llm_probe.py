"""NODE B reachability probe. Phase 0.5.

This module is where RULE 2 is enforced in code:

    The safety property must never depend on the network between nodes.

``/api/health`` must return 200 whether or not NODE B is alive. So the probe

* is cached (default 30 s), so health checks do not hammer the LAN,
* uses a short timeout of its own, unrelated to the 30 s generation timeout,
* never raises — an unreachable NODE B is a *reported state*, not an error,
* never blocks: a refresh already in flight is awaited by one caller only,
  and everyone else is served the last known value immediately.

If this file ever starts raising or blocking, NODE B can take NODE A down
with it, and the whole two-node design is void.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services import settings_store

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LlmStatus:
    reachable: bool
    host: str
    model: str
    latency_ms: int | None = None
    error: str | None = None
    checked_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "reachable": self.reachable,
            "host": self.host,
            "model": self.model,
        }
        if self.latency_ms is not None:
            out["latency_ms"] = self.latency_ms
        if self.error:
            out["error"] = self.error
        return out


# How long the resolved kill-switch value is trusted before re-reading the
# table. The admin endpoint promises the switch "takes effect within 10
# seconds", so this must stay comfortably under that.
KILL_SWITCH_CACHE_S = 5.0


class LlmProbe:
    """Cached, non-blocking reachability check for NODE B."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._settings = settings
        self._cached: LlmStatus | None = None
        self._lock = asyncio.Lock()
        # Resolving the kill switch needs the database, because the switch is
        # configuration and **configuration lives in tables, never in code**.
        # Optional so unit tests can build a probe with no database at all.
        self._sessionmaker = sessionmaker
        self._enabled_cached: bool | None = None
        self._enabled_at: float = 0.0

    async def _enabled(self) -> bool:
        """Is inference allowed right now?

        ⚠️ **This used to read ``settings.llm_enabled`` directly, and that was a
        defect worth remembering.** ``Settings`` is loaded from the environment
        **once, at process start**; the admin kill switch writes to the
        ``system_settings`` table. So the switch updated the database, the admin
        screen dutifully displayed "off" — and inference carried on running,
        because nothing connected the two. An administrator turning AI off at
        3 a.m. would have been told it was off while it was not.

        Found 2026-09-14 by driving the real endpoint and watching
        ``/api/health`` never change.

        The table is read at most every ``KILL_SWITCH_CACHE_S`` seconds, and a
        database failure falls back to the environment value rather than
        raising — this function is on the health path and **must never be able
        to take NODE A down**.
        """
        if self._sessionmaker is None:
            return self._settings.llm_enabled

        now = time.monotonic()
        if (
            self._enabled_cached is not None
            and (now - self._enabled_at) < KILL_SWITCH_CACHE_S
        ):
            return self._enabled_cached

        try:
            async with self._sessionmaker() as session:
                resolved = await settings_store.llm_enabled(
                    session, env_default=self._settings.llm_enabled
                )
        except Exception as exc:
            log.warning("kill_switch_lookup_failed", error=str(exc))
            return (
                self._enabled_cached
                if self._enabled_cached is not None
                else (self._settings.llm_enabled)
            )

        self._enabled_cached = resolved
        self._enabled_at = now
        return resolved

    def invalidate_kill_switch(self) -> None:
        """Drop the cached switch value so the next read hits the table.

        Called by the admin endpoint the moment the switch is flipped, so the
        change is visible immediately in *this* process rather than up to
        ``KILL_SWITCH_CACHE_S`` later. Other processes pick it up via the TTL,
        which is why the TTL is the guarantee and this is only an optimisation.
        """
        self._enabled_cached = None
        self._enabled_at = 0.0

    @property
    def _host(self) -> str:
        url = self._settings.llm_base_url
        return url.removeprefix("http://").removeprefix("https://")

    def _disabled_status(self) -> LlmStatus:
        return LlmStatus(
            reachable=False,
            host=self._host,
            model=self._settings.llm_model,
            error="disabled_by_kill_switch",
        )

    def _is_fresh(self, status: LlmStatus | None) -> bool:
        if status is None:
            return False
        return (time.monotonic() - status.checked_at) < self._settings.llm_probe_cache_s

    async def status(self) -> LlmStatus:
        """Return NODE B's state. Never raises, never blocks on the network."""
        # Admin kill switch: forces the degraded path without touching the LAN.
        # Resolved from the table, not from the process's start-up environment.
        if not await self._enabled():
            return self._disabled_status()

        if self._is_fresh(self._cached):
            assert self._cached is not None
            return self._cached

        # One caller refreshes; the rest are served the stale value rather than
        # queueing behind a network call. A slightly stale reachability flag is
        # always preferable to a health endpoint that hangs.
        if self._lock.locked():
            return self._cached or LlmStatus(
                reachable=False,
                host=self._host,
                model=self._settings.llm_model,
                error="probe_in_flight",
            )

        async with self._lock:
            if self._is_fresh(self._cached):
                assert self._cached is not None
                return self._cached
            self._cached = await self._probe()
            return self._cached

    async def _probe(self) -> LlmStatus:
        url = f"{self._settings.llm_base_url.rstrip('/')}/api/tags"
        started = time.perf_counter()
        try:
            timeout = self._settings.llm_probe_timeout_s
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                return LlmStatus(
                    reachable=True,
                    host=self._host,
                    model=self._settings.llm_model,
                    latency_ms=elapsed_ms,
                )
        # Catching broadly is the point: an unreachable NODE B is a state to
        # report, not a failure to raise. Narrowing this would let some
        # unforeseen httpx error escape and take /api/health down with it,
        # which is precisely the RULE 2 violation this module exists to stop.
        except Exception as exc:
            # Logged as info, not error: NODE B being off is an expected,
            # designed-for condition. Phase 10.4 treats it as a low-priority
            # alert because it is not an outage.
            log.info("nodeb_unreachable", host=self._host, error=type(exc).__name__)
            return LlmStatus(
                reachable=False,
                host=self._host,
                model=self._settings.llm_model,
                error=type(exc).__name__,
            )

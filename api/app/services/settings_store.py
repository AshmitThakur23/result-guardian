"""Runtime settings held in a table. Phase 5.4.

    Admin: … **NODE B status and kill switch (``LLM_ENABLED``)**

CLAUDE.md: *"Configuration lives in tables, never in code. Thresholds,
escalation delays, keywords, synonyms — an admin must be able to edit them.
Hardcoding any of these turns the product back into a demo."*

``RG_LLM_ENABLED`` in the environment is the **default**; a row in
``system_settings`` overrides it. That ordering matters: a fresh deployment
works with no rows at all, and an admin flipping the switch does not need a
container restart or a shell.

**The cache is deliberately short and deliberately not invalidated across
processes.** Ten seconds, checked against the wall clock. A kill switch that
takes up to ten seconds to reach the worker is fine; one that depends on a
pub/sub channel working during the incident that made someone reach for it is
not.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import uuid7

log = structlog.get_logger(__name__)

CACHE_TTL_SECONDS = 10.0

KEY_LLM_ENABLED = "llm_enabled"
KEY_LLM_KILL_REASON = "llm_kill_switch_reason"

# Only these may be written through the admin API. An open key/value store
# reachable by HTTP is a configuration injection waiting to happen.
EDITABLE_KEYS = {
    KEY_LLM_ENABLED: "bool",
    KEY_LLM_KILL_REASON: "string",
}


@dataclass
class _Cached:
    values: dict[str, tuple[str, str]]
    fetched_at: float


_cache: _Cached | None = None


def _coerce(value: str, value_type: str) -> Any:
    if value_type == "bool":
        return value.strip().lower() in {"1", "true", "t", "yes", "on"}
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    return value


async def _load(
    session: AsyncSession, *, force: bool = False
) -> dict[str, tuple[str, str]]:
    global _cache
    now = time.monotonic()
    if not force and _cache is not None and now - _cache.fetched_at < CACHE_TTL_SECONDS:
        return _cache.values

    try:
        rows = (
            await session.execute(
                text(
                    "SELECT key, value, value_type FROM system_settings "
                    " WHERE deleted_at IS NULL"
                )
            )
        ).all()
    except Exception:
        # The table arrives in migration 0011. Between `compose up` and
        # `alembic upgrade head` it does not exist, and a missing settings
        # table must degrade to "use the environment defaults", never to a
        # 500 on every request.
        await session.rollback()
        log.info("system_settings_unavailable")
        return _cache.values if _cache else {}

    values = {r.key: (r.value, r.value_type) for r in rows}
    _cache = _Cached(values=values, fetched_at=now)
    return values


async def get(
    session: AsyncSession, key: str, default: Any = None, *, force: bool = False
) -> Any:
    values = await _load(session, force=force)
    if key not in values:
        return default
    raw, value_type = values[key]
    try:
        return _coerce(raw, value_type)
    except (TypeError, ValueError):
        log.warning("system_setting_unparseable", key=key, value_type=value_type)
        return default


async def llm_enabled(session: AsyncSession, *, env_default: bool) -> bool:
    """The kill switch, resolved. Table first, environment second."""
    return bool(await get(session, KEY_LLM_ENABLED, default=env_default))


async def set_value(
    session: AsyncSession,
    key: str,
    value: Any,
    *,
    actor_user_id: Any = None,
) -> tuple[str, str]:
    """Write a setting. **Does not commit** — the caller pairs it with an
    audit row in the same transaction."""
    if key not in EDITABLE_KEYS:
        raise KeyError(key)
    value_type = EDITABLE_KEYS[key]
    rendered = ("true" if value else "false") if value_type == "bool" else str(value)

    await session.execute(
        text(
            "INSERT INTO system_settings (id, key, value, value_type, updated_by) "
            "VALUES (CAST(:id AS uuid), :k, :v, :t, CAST(:a AS uuid)) "
            "ON CONFLICT (key) DO UPDATE "
            "   SET value = EXCLUDED.value, updated_at = now(), "
            "       updated_by = EXCLUDED.updated_by"
        ),
        {
            # UUIDv7 in Python, per the house convention -- there is no
            # server-side default on this column.
            "id": str(uuid7()),
            "k": key,
            "v": rendered,
            "t": value_type,
            "a": str(actor_user_id) if actor_user_id else None,
        },
    )
    invalidate()
    return rendered, value_type


def invalidate() -> None:
    """Drop the cache. Called on write, and by tests."""
    global _cache
    _cache = None

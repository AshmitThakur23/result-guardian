"""Phase 0 tests.

The important one is ``test_health_ok_when_nodeb_unreachable``: it asserts
RULE 2 at the HTTP boundary. If that test ever fails, NODE B has become load
bearing and the two-node design is broken.

CI must pass with NODE B unreachable, so no test here may contact a real LLM.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.services import llm_probe
from app.services.llm_probe import LlmProbe


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "llm_base_url": "http://127.0.0.1:59999",  # nothing listens here
        "llm_enabled": True,
        "llm_probe_timeout_s": 0.25,
        "llm_probe_cache_s": 30.0,
        "llm_model": "qwen3:4b",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def test_probe_reports_unreachable_without_raising() -> None:
    probe = LlmProbe(_settings())
    status = await probe.status()
    assert status.reachable is False
    assert status.error  # the reason is reported, not swallowed
    assert status.host == "127.0.0.1:59999"


async def test_probe_honours_kill_switch_without_network_call() -> None:
    probe = LlmProbe(
        _settings(llm_enabled=False, llm_base_url="http://10.255.255.1:11434")
    )
    status = await probe.status()
    assert status.reachable is False
    assert status.error == "disabled_by_kill_switch"


# ── the kill switch actually reaching the runtime ─────────────────────
#
# Found 2026-09-14 by driving the real admin endpoint against the real stack:
# POST /api/admin/node-b/kill-switch wrote llm_enabled=false to the table, the
# admin screen reported "off" -- and /api/health went on saying
# reachable: true, degraded_features: [] indefinitely. The endpoint's own
# summary promises the switch "takes effect within 10 seconds".
#
# Cause: the probe read `settings.llm_enabled`, which is loaded from the
# environment ONCE at process start, while the switch writes to the
# `system_settings` table. Nothing connected the two. An administrator turning
# AI off at 3am would have been told it was off while it kept running.
#
# CLAUDE.md: configuration lives in tables, never in code -- and a value in a
# table that nothing reads is the same defect as a value in code.


class _FakeSession:
    """Just enough to satisfy ``async with sessionmaker() as session``."""

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def test_the_kill_switch_in_the_table_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ The regression test for the defect above.

    Environment says enabled, table says disabled. The table must win.
    Without the fix the probe never consults the table at all, tries the
    network, and reports a connection error instead of the kill switch.
    """
    seen: list[bool] = []

    async def _disabled_in_the_table(session: object, *, env_default: bool) -> bool:
        seen.append(env_default)
        return False

    monkeypatch.setattr(llm_probe.settings_store, "llm_enabled", _disabled_in_the_table)

    probe = LlmProbe(
        _settings(llm_enabled=True),  # the ENVIRONMENT says on
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
    )
    status = await probe.status()

    assert status.reachable is False
    # The discriminating assertion: not merely unreachable, but unreachable
    # *for this reason*. A network error here would mean the switch was ignored.
    assert status.error == "disabled_by_kill_switch"
    assert seen == [True], "the environment value must be passed as the fallback"


async def test_a_database_failure_never_takes_the_health_endpoint_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RULE 2's floor. Resolving the switch is on the health path, so it must
    degrade to the environment value rather than raise."""

    async def _boom(session: object, *, env_default: bool) -> bool:
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(llm_probe.settings_store, "llm_enabled", _boom)

    probe = LlmProbe(
        _settings(llm_enabled=False),
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
    )
    status = await probe.status()  # must not raise

    assert status.reachable is False
    assert status.error == "disabled_by_kill_switch"  # fell back to the env value


async def test_flipping_the_switch_is_visible_immediately_in_this_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admin endpoint invalidates the cached value, so an admin who flips
    the switch sees the change on the very next request rather than up to
    KILL_SWITCH_CACHE_S later."""
    enabled = {"value": False}

    async def _from_the_table(session: object, *, env_default: bool) -> bool:
        return enabled["value"]

    monkeypatch.setattr(llm_probe.settings_store, "llm_enabled", _from_the_table)

    probe = LlmProbe(
        _settings(llm_enabled=True),
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type,return-value]
    )
    assert (await probe.status()).error == "disabled_by_kill_switch"

    # Table flips back on. Without invalidation the cached "off" would stand.
    enabled["value"] = True
    probe.invalidate_kill_switch()
    status = await probe.status()
    assert status.error != "disabled_by_kill_switch"


async def test_probe_caches_so_health_does_not_hammer_the_lan() -> None:
    probe = LlmProbe(_settings())
    first = await probe.status()
    second = await probe.status()
    assert first.checked_at == second.checked_at  # same cached object


async def test_probe_never_blocks_longer_than_its_own_timeout() -> None:
    probe = LlmProbe(_settings(llm_probe_timeout_s=0.25))
    start = asyncio.get_running_loop().time()
    await probe.status()
    elapsed = asyncio.get_running_loop().time() - start
    # Must not inherit the 30s generation timeout.
    assert elapsed < 5.0


# No @pytest.mark.anyio: asyncio_mode="auto" already runs every async test in
# this file, and the marker would hand this one test to a second async plugin
# under --strict-markers. The other tests here have never carried it.
async def test_health_ok_when_nodeb_unreachable() -> None:
    """RULE 2 at the HTTP boundary: NODE B down must not fail /api/health."""
    from app.main import create_app

    app = create_app()
    app.state.llm_probe = LlmProbe(_settings())

    # Stub the DB dependency: this test is about NODE B, not Postgres.
    from app.db.session import get_session

    async def _fake_session():  # type: ignore[no-untyped-def]
        class _Result:
            def scalar(self) -> int:
                return 3

            def first(self) -> None:
                return None

        class _Session:
            async def execute(self, *_: object, **__: object) -> _Result:
                return _Result()

        yield _Session()

    app.dependency_overrides[get_session] = _fake_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["llm"]["reachable"] is False
    assert "llm_generation" in body["degraded_features"]


async def test_missing_worker_health_does_not_report_the_database_as_down() -> None:
    """An absent worker_health table must degrade to "worker", not "database".

    Regression. Both queries once shared one try block, so a missing
    worker_health set db_ok False and returned 503 -- failing Exit Gate 0 and
    the RULE 2 assertion above, over a table that says nothing about whether
    Postgres is reachable. It is also the real state of the system between
    `docker compose up` and `alembic upgrade head`.
    """
    from app.db.session import get_session
    from app.main import create_app

    app = create_app()
    app.state.llm_probe = LlmProbe(_settings())

    async def _fake_session():  # type: ignore[no-untyped-def]
        calls = 0

        class _Ok:
            def scalar(self) -> int:
                return 3

        class _Session:
            async def execute(self, *_: object, **__: object) -> _Ok:
                nonlocal calls
                calls += 1
                if calls == 1:  # SELECT 1 -- Postgres is fine
                    return _Ok()
                raise RuntimeError('relation "worker_health" does not exist')

            async def rollback(self) -> None:
                return None

        yield _Session()

    app.dependency_overrides[get_session] = _fake_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "ok"
    assert body["status"] == "ok"
    assert body["worker_heartbeat_age_s"] is None
    assert "worker" in body["degraded_features"]
    assert "database" not in body["degraded_features"]

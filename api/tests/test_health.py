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
    probe = LlmProbe(_settings(llm_enabled=False, llm_base_url="http://10.255.255.1:11434"))
    status = await probe.status()
    assert status.reachable is False
    assert status.error == "disabled_by_kill_switch"


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


@pytest.mark.anyio
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

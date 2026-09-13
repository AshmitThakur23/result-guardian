"""Application settings. Phase 0.5.

All settings are read from the environment with an ``RG_`` prefix, so nothing
is hardcoded and NODE B can be retargeted with a single variable.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── environment ───────────────────────────────────────────────
    env: str = "dev"
    log_level: str = "INFO"
    tz: str = "Asia/Kolkata"
    version: str = "0.1.0"
    git_sha: str = "unknown"

    # ── database ──────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://rg_app:change-me@localhost:5432/result_guardian"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5

    # ── auth ──────────────────────────────────────────────────────
    jwt_secret: str = "change-me"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_hours: int = 12
    # Phase 5.7: "Rate limiting on auth endpoints." A tuning knob, not a
    # clinical threshold, so it lives with the other deployment settings
    # rather than in a table -- but it does NOT live hardcoded in the
    # limiter, which is what CLAUDE.md's "configuration lives in tables,
    # never in code" is really guarding against.
    #
    # 20/minute/address is far above a human typing a password and far below
    # what a credential spray needs to be useful. Raise it only where many
    # real users share one apparent address, and say why.
    auth_rate_limit_per_minute: int = 20
    # Shared secret for the SMS provider's delivery-receipt webhook -- the one
    # endpoint with no user behind it. Empty means the webhook is accepted
    # unauthenticated and logs a warning on every call; see
    # `app/routers/webhooks.py` for why it is not simply mandatory.
    webhook_secret: str = ""

    # ── NODE B ────────────────────────────────────────────────────
    # Use the literal LAN IP. Inside the API container, "localhost" and
    # "host.docker.internal" do not reach NODE B.
    llm_base_url: str = "http://192.168.1.50:11434"
    llm_enabled: bool = True
    llm_timeout_s: float = 30.0
    llm_model: str = "qwen3:4b"
    # The probe is cached so /api/health never pays for a network round trip
    # more than once per window, and never blocks on NODE B.
    llm_probe_cache_s: float = 30.0
    llm_probe_timeout_s: float = 2.0

    # ── web ───────────────────────────────────────────────────────
    cors_origins: str = "http://localhost,http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.env.lower() in {"prod", "production"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

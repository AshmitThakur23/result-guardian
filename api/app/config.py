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

    # ── document ingestion (Phase 6) ──────────────────────────────
    # Where files actually live. 6.1: "DB holds the path, never the blob."
    # These are deployment facts -- which volume, which mount -- not clinical
    # configuration, so they belong in the environment. The *thresholds* that
    # decide whether a page is readable do not; they live in `system_settings`
    # so an admin can move them without a redeploy. See
    # `app/services/documents/settings.py`.
    document_root: str = "/data/documents"
    inbox_dir: str = "/data/inbox"
    processing_dir: str = "/data/processing"
    archive_dir: str = "/data/archive"
    # 6.1: "multipart, max 25 MB".
    upload_max_bytes: int = 25 * 1024 * 1024

    # 6.1: "Virus scan hook (ClamAV container) **before** processing."
    # Empty host means no scanner is deployed. That is not the same as "the
    # file is clean", and `app/services/documents/scan.py` is careful about
    # the difference -- it records `skipped`, never `clean`.
    clamav_host: str = ""
    clamav_port: int = 3310
    clamav_timeout_s: float = 30.0
    # When a scanner IS configured but unreachable, refuse the file rather
    # than processing it unscanned. Set false only in a deployment that has
    # accepted that risk in writing.
    clamav_required: bool = True

    # The watched folder is optional: a hospital that only ever uses the
    # upload form should not have a poller walking a directory that will
    # never exist.
    watched_folder_enabled: bool = False
    watched_folder_poll_s: float = 5.0

    # 6.4 renders page images for the overlay and for the manual-entry
    # fallback. 200 DPI is the plan's own floor for OCR input.
    page_render_dpi: int = 200

    # ── NODE B ────────────────────────────────────────────────────
    # Use the literal LAN IP. Inside the API container, "localhost" and
    # "host.docker.internal" do not reach NODE B.
    llm_base_url: str = "http://192.168.1.50:11434"
    llm_enabled: bool = True
    llm_timeout_s: float = 30.0
    llm_model: str = "qwen3:4b"

    #: The model Phase 8 uses to *phrase* retrieved guidance. Separate from
    #: `llm_model` above, which names what the health probe reports.
    #:
    #: ⚠️ **Not qwen3, and the reason is measured.** qwen3 is a reasoning model:
    #: asked for two sentences over one short source it spent **2,048 tokens and
    #: 82.5 s thinking**, hit the token cap mid-monologue, and returned no JSON
    #: at all. `mistral:7b` — already resident on NODE B — did the same job in
    #: **17.5 s** with a clean answer and a verbatim quote.
    #:
    #: The generation step is *phrasing text that has already been retrieved*.
    #: Reasoning is not merely unnecessary for it; it is 4.7x the cost for a
    #: worse result. Measured 2026-09-15 against the live NODE B.
    llm_generation_model: str = "mistral:7b"
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

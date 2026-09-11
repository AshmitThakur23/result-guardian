# Result Guardian — API + worker image (same image, different entrypoint).
# Python 3.11 per the locked stack, regardless of the host's Python.

# ── builder ───────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml ./
RUN pip install --no-cache-dir build hatchling \
    && pip wheel --no-cache-dir --wheel-dir /wheels \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.3" \
        "sqlalchemy[asyncio]>=2.0" \
        "asyncpg>=0.29" \
        "alembic>=1.13" \
        "httpx>=0.27" \
        "structlog>=24.1" \
        "uuid7>=0.1"

# ── runtime base ──────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 1000 rg && useradd --uid 1000 --gid rg --create-home rg

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

COPY --chown=rg:rg . /app

USER rg
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── dev ───────────────────────────────────────────────────────────
# Adds test/lint tooling. docker-compose.override.yml targets this stage.
FROM runtime AS dev

USER root
RUN pip install --no-cache-dir \
        "pytest>=8.0" \
        "pytest-asyncio>=0.23" \
        "pytest-cov>=5.0" \
        "ruff>=0.5" \
        "black>=24.4" \
        "mypy>=1.10"
USER rg

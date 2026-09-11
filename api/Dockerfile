# Result Guardian — API + worker image (same image, different entrypoint).
# Python 3.11 per the locked stack, regardless of the host's Python.

# ── builder ───────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencies come from pyproject.toml and nowhere else. A hand-maintained
# second copy of the list here drifts the moment someone adds a dependency,
# and the image silently ships without it.
#
# Only the files the build backend needs are copied, so editing application
# code does not invalidate the dependency layer.
COPY pyproject.toml ./
COPY app ./app
COPY worker ./worker
RUN pip install --no-cache-dir hatchling \
    && pip wheel --no-cache-dir --wheel-dir /wheels .

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
#
# ⚠️ This is the LAST stage, so `docker build` with no --target builds THIS
# one, not `runtime`. docker-compose.yml therefore pins `target: runtime`
# explicitly -- without it, production ships pytest, ruff and mypy onto a
# hospital server. Phase 10.1 least-privilege. Do not append a stage below
# this one without re-checking every build that omits --target.
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

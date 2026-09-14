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

# Phase 6 system libraries, and only these two:
#
#   libmagic1  — python-magic is a thin binding over libmagic. Without it the
#                import fails at startup, not at first upload, so a missing
#                library would take the whole API down rather than degrade
#                one endpoint. 6.1 sniffs MIME because the plan says "do not
#                trust the extension".
#   libgl1 / libglib2.0-0 — opencv-python-headless still links libGL and
#                glib. "headless" removes the GUI *windowing*, not these.
#   libgomp1   — paddlepaddle's compiled core links OpenMP. Without it the
#                import fails with `libgomp.so.1: cannot open shared object
#                file`, which reads like a Python problem and is not one.
#                Found by running it, not by reading the docs.
#
# The OCR engine itself is pip-installed and pinned in pyproject.toml, so the
# image stays reproducible from one file; only its shared libraries are here.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libmagic1 \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

COPY --chown=rg:rg . /app

# Development-only code must not reach a hospital server. `scripts/` holds the
# Phase 1.5 seeder, which writes twenty fake patients; it refuses to run when
# RG_ENV=prod, but the safer position is for it not to be on the machine at
# all. `tests/` is the same argument, minus the fake patients.
#
# Removed here rather than via .dockerignore because the `dev` stage below
# genuinely needs both, and .dockerignore applies to the whole build.
# `alembic/` stays: migrations run in production.
RUN rm -rf /app/scripts /app/tests

# ── Phase 6: bake the OCR models into the image ───────────────────
#
# PaddleOCR downloads its detection and recognition models on **first use**,
# into ~/.paddlex. On a hospital server with no internet — which is the
# deployment this whole product is designed for — that download never
# succeeds, so OCR would be permanently unavailable on exactly the machines
# it was built for. The failure would be quiet, too: the pipeline degrades to
# `needs_review` by design, so it looks like poor accuracy rather than a
# missing model.
#
# Downloading at build time, on a machine that does have internet, makes the
# image self-contained. It also makes the first upload fast instead of
# mysteriously slow.
#
# `|| true`: a build behind a proxy that cannot reach the model host must
# still produce a working image. OCR then degrades at runtime exactly as it
# does when the engine is missing, and the document still reaches a human.
USER rg
RUN python -c "\
from paddleocr import PaddleOCR; \
PaddleOCR(lang='en', use_textline_orientation=True, enable_mkldnn=False, \
          use_doc_orientation_classify=False, use_doc_unwarping=False)" \
    || echo 'WARNING: OCR models were not pre-downloaded; OCR will degrade at runtime'
USER root

# Phase 6's document tree, created here rather than at runtime.
#
# Docker seeds an EMPTY named volume from whatever the image has at the mount
# point, ownership included. Creating these directories as `rg` in the image
# is therefore what makes them writable by the unprivileged user once
# docker-compose.yml mounts `rgdocs` over /data. Create them at runtime
# instead and the volume is seeded from an empty root-owned directory, so the
# first upload fails with EACCES -- as a 500, on a hospital's first try.
RUN mkdir -p /data/documents /data/inbox /data/processing /data/archive \
    && chown -R rg:rg /data

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

# Put back what the runtime stage stripped. The dev image is where the suite
# runs and where `python -m scripts.seed_dev` is invoked.
COPY --chown=rg:rg tests /app/tests
COPY --chown=rg:rg scripts /app/scripts

USER rg

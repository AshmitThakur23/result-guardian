# Result Guardian — NODE A database image
#
# The build plan specifies `pgvector/pgvector:pg16`, but that image ships ONLY
# pgvector. Phase 0.2 also requires pgmq (queue + SLA timers) and pg_cron
# (sweeps that re-fire overdue timers after a reboot). Both are load-bearing:
# Phase 2's durability guarantee is pg_cron, and Phase 4's escalation ladder
# rides pgmq. So we extend the base image rather than drop the requirement.
#
# See docs/adr/0001-single-postgres.md.

FROM pgvector/pgvector:pg16

ARG PGMQ_VERSION=1.4.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-cron \
        build-essential \
        ca-certificates \
        curl \
        postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

# pgmq is not packaged in Debian; build the extension from source.
#
# ⚠️ D9. `make install` ALONE SILENTLY PRODUCES A BROKEN EXTENSION.
#
# pgmq ships only UPGRADE scripts (sql/pgmq--X--Y.sql). The base install
# script, sql/pgmq--<version>.sql, does not exist in the tarball -- upstream's
# Makefile GENERATES it in the `all` target by copying sql/pgmq.sql. Two things
# then go wrong at once:
#
#   1. PGXS's `install` target does not depend on `all`, so running
#      `make install` never generates the base script; and
#   2. `DATA = $(wildcard sql/*--*.sql)` is expanded when make PARSES the
#      Makefile, so even if the file appeared later it would not be installed.
#
# The result is an image that builds cleanly while shipping pgmq.control with
# no matching install script. Postgres reads default_version from the control
# file, finds no pgmq--<version>.sql, and the FIRST BOOT dies with
# "extension pgmq has no installation script nor update path for version ...".
# That took down the whole init and with it every queue and timer.
#
# So: generate the base script first, then install, then ASSERT the two files
# Postgres will actually look for are present -- failing the BUILD rather than
# the first boot if upstream's layout ever changes again.
RUN set -eux; \
    curl -fsSL "https://github.com/pgmq/pgmq/archive/refs/tags/v${PGMQ_VERSION}.tar.gz" -o /tmp/pgmq.tar.gz; \
    mkdir -p /tmp/pgmq; \
    tar -xzf /tmp/pgmq.tar.gz -C /tmp/pgmq --strip-components=1; \
    cd /tmp/pgmq/pgmq-extension; \
    # Take the version from the control file, not the git tag: Postgres looks
    # for pgmq--<default_version>.sql, and only the control file defines that.
    extver="$(sed -n "s/^default_version[^']*'\([^']*\)'.*/\1/p" pgmq.control)"; \
    test -n "$extver"; \
    cp sql/pgmq.sql "sql/pgmq--$extver.sql"; \
    make install; \
    sharedir="$(pg_config --sharedir)"; \
    test -f "$sharedir/extension/pgmq.control"; \
    test -f "$sharedir/extension/pgmq--$extver.sql"; \
    echo "pgmq $extver installed and verified"; \
    cd /; \
    rm -rf /tmp/pgmq /tmp/pgmq.tar.gz

# Build tooling is not needed at runtime and is a needless attack surface
# on a hospital server (Phase 10.1 least-privilege posture).
RUN apt-get purge -y --auto-remove build-essential postgresql-server-dev-16 curl \
    && rm -rf /var/lib/apt/lists/*

# pg_cron must be preloaded at server start; it cannot be enabled per-session,
# and it can only be created in the ONE database named by cron.database_name.
#
# This CMD is only the default for running the image bare. docker-compose.yml
# overrides it with `command:` so that cron.database_name is interpolated from
# POSTGRES_DB -- an exec-form CMD cannot expand a variable, and a hardcoded
# name here silently diverges the moment POSTGRES_DB is changed. The guard in
# init/01-extensions.sql catches any divergence at boot.
CMD ["postgres", \
     "-c", "shared_preload_libraries=pg_cron", \
     "-c", "cron.database_name=result_guardian"]

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
RUN curl -fsSL "https://github.com/pgmq/pgmq/archive/refs/tags/v${PGMQ_VERSION}.tar.gz" -o /tmp/pgmq.tar.gz \
    && mkdir -p /tmp/pgmq && tar -xzf /tmp/pgmq.tar.gz -C /tmp/pgmq --strip-components=1 \
    && make -C /tmp/pgmq/pgmq-extension install \
    && rm -rf /tmp/pgmq /tmp/pgmq.tar.gz

# Build tooling is not needed at runtime and is a needless attack surface
# on a hospital server (Phase 10.1 least-privilege posture).
RUN apt-get purge -y --auto-remove build-essential postgresql-server-dev-16 curl \
    && rm -rf /var/lib/apt/lists/*

# pg_cron must be preloaded at server start; it cannot be enabled per-session.
# cron.database_name pins the scheduler to our database.
CMD ["postgres", \
     "-c", "shared_preload_libraries=pg_cron", \
     "-c", "cron.database_name=result_guardian"]

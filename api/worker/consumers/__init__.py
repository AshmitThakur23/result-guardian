"""Per-queue message handlers.

One module per queue, each exposing a single async ``handle_*`` function with
the signature ``worker.consumer.Handler`` expects. Handlers must be
idempotent: pgmq delivers at least once, and a duplicate delivery has to
produce the same state as the first.
"""

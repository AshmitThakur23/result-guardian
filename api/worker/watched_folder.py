"""The watched-folder consumer. Phase 6.1.

    Watched folder consumer — polls `/data/inbox`, moves to `/data/processing`
    then `/data/archive/YYYY/MM/DD`

This is how a lab that cannot integrate hands over results: it drops files on
a share. That makes it the least controlled input the system has, and the
directory dance is what makes it safe.

**Why three directories and not a flag column.** A file being written to the
share is visible in the directory before it is complete. Moving it to
``processing`` first is an atomic rename within one filesystem, so a file is
either wholly in ``inbox`` or wholly in ``processing`` and never half-read —
and a worker that dies mid-file leaves it in ``processing`` where it is
obviously stranded, rather than in ``inbox`` where it would be picked up again
and again.

**Why the file must stop changing first.** A rename is atomic; an upload is
not. A file whose size is still growing is still arriving, so it is skipped
until its size and mtime have held still for :data:`SETTLE_SECONDS`. Without
this, a slow copy over SMB is ingested as a truncated PDF — which parses, and
produces half a report.

**Archive, never delete.** The file is the clinical record. Content addressing
already keeps a copy in the document store, and the dated archive keeps the
original as the lab sent it, name and all.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import shutil
from pathlib import Path

import structlog

from app.config import get_settings
from app.db.session import get_sessionmaker
from app.services.documents import intake

log = structlog.get_logger(__name__)

# A file must be this old, and this unchanged, before it is touched.
SETTLE_SECONDS = 5.0

# Only files a lab plausibly sends. Everything else is left alone rather than
# rejected, because a share also collects thumbnails, lock files and
# `.DS_Store`, and moving those to a failed directory would be noise.
WATCHED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

# Partial-transfer markers used by common copy tools. Never touched.
IGNORED_PREFIXES = (".", "~", "_")
IGNORED_SUFFIXES = (".part", ".crdownload", ".tmp", ".filepart")


class WatchedFolder:
    def __init__(self, shutdown: asyncio.Event) -> None:
        settings = get_settings()
        self.inbox = Path(settings.inbox_dir)
        self.processing = Path(settings.processing_dir)
        self.archive = Path(settings.archive_dir)
        self.poll_seconds = settings.watched_folder_poll_s
        self.shutdown = shutdown
        self.log = log.bind(inbox=str(self.inbox))

    async def run(self) -> None:
        self.log.info("watched_folder_started")
        # Recover anything a previous run left mid-flight before taking new
        # work. A file stranded in `processing` is one nobody would otherwise
        # ever look at again.
        await self._requeue_stranded()

        while not self.shutdown.is_set():
            try:
                handled = await self._sweep()
            except Exception:
                self.log.exception("watched_folder_sweep_failed")
                handled = 0
            if not handled:
                await self._sleep_or_stop(self.poll_seconds)
        self.log.info("watched_folder_stopped")

    async def _sleep_or_stop(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.shutdown.wait(), timeout=seconds)

    async def _requeue_stranded(self) -> None:
        if not self.processing.exists():
            return
        for path in sorted(self.processing.iterdir()):
            if path.is_file():
                self.log.warning("requeueing_stranded_file", file=path.name)
                with contextlib.suppress(OSError):
                    shutil.move(str(path), str(self.inbox / path.name))

    async def _sweep(self) -> int:
        if not self.inbox.exists():
            return 0

        handled = 0
        for path in sorted(self.inbox.iterdir()):
            if self.shutdown.is_set():
                break
            if not self._is_candidate(path):
                continue
            if not await self._has_settled(path):
                continue
            await self._ingest_one(path)
            handled += 1
        return handled

    def _is_candidate(self, path: Path) -> bool:
        if not path.is_file():
            return False
        name = path.name
        if name.startswith(IGNORED_PREFIXES) or name.lower().endswith(IGNORED_SUFFIXES):
            return False
        return path.suffix.lower() in WATCHED_SUFFIXES

    async def _has_settled(self, path: Path) -> bool:
        """Size and mtime unchanged across one settle interval."""
        try:
            before = path.stat()
        except OSError:
            return False
        await asyncio.sleep(SETTLE_SECONDS)
        try:
            after = path.stat()
        except OSError:
            # Moved or removed while we waited. Someone else has it.
            return False
        return (before.st_size, before.st_mtime) == (after.st_size, after.st_mtime)

    async def _ingest_one(self, path: Path) -> None:
        self.processing.mkdir(parents=True, exist_ok=True)
        claimed = self.processing / path.name
        try:
            # Atomic within a filesystem: from here on the file is ours and
            # no second worker can claim it.
            path.rename(claimed)
        except OSError as exc:
            self.log.info("claim_failed", file=path.name, error=str(exc))
            return

        entry = self.log.bind(file=path.name)
        try:
            data = claimed.read_bytes()
        except OSError:
            entry.exception("watched_file_unreadable")
            self._archive(claimed, failed=True)
            return

        sessionmaker = get_sessionmaker()
        try:
            async with sessionmaker() as session:
                accepted = await intake.accept_upload(
                    session,
                    data=data,
                    filename=path.name,
                    source_channel="watched_folder",
                    # No user: the lab dropped this on a share. `uploaded_by`
                    # stays NULL rather than being attributed to a service
                    # account that did not decide anything.
                    uploaded_by=None,
                )
                await session.commit()
        except intake.IntakeRejectedError as exc:
            # A refused file must not silently vanish and must not be retried
            # for ever. Archive it as failed, where an admin can find it.
            entry.warning("watched_file_rejected", reason=exc.reason)
            self._archive(claimed, failed=True)
            return
        except Exception:
            entry.exception("watched_file_intake_failed")
            # Put it back: this is an infrastructure failure, not a bad file,
            # and the next sweep should try again.
            with contextlib.suppress(OSError):
                shutil.move(str(claimed), str(self.inbox / path.name))
            return

        entry.info(
            "watched_file_accepted",
            document_id=str(accepted.document_id),
            duplicate=accepted.duplicate,
        )
        self._archive(claimed, failed=False)

    def _archive(self, path: Path, *, failed: bool) -> None:
        """Move to ``archive/YYYY/MM/DD/`` — or ``archive/failed/`` — keeping
        the original name."""
        today = dt.datetime.now(dt.UTC)
        target_dir = (
            self.archive / "failed" / today.strftime("%Y/%m/%d")
            if failed
            else self.archive / today.strftime("%Y/%m/%d")
        )
        target_dir.mkdir(parents=True, exist_ok=True)

        target = target_dir / path.name
        if target.exists():
            # Same filename, same day, different file. Never overwrite: the
            # lab's own naming is not unique and the loser would be gone.
            stem, suffix = target.stem, target.suffix
            target = target_dir / f"{stem}.{today.strftime('%H%M%S%f')}{suffix}"
        try:
            shutil.move(str(path), str(target))
        except OSError:
            self.log.exception("archive_failed", file=path.name)

"""Content-addressed file storage. Phase 6.1.

    Store original file on disk with content-addressed name `sha256.pdf`;
    **DB holds the path, never the blob**

Two properties fall out of naming a file after its own hash, and both are
load-bearing:

1. **Deduplication is free.** The same report uploaded twice lands on the same
   path. There is no comparison pass, no "is this the same document?" heuristic
   — the filesystem answers it.
2. **Tampering is detectable.** ``verify()`` re-hashes the bytes on disk and
   compares them with the name. A report that changed after it was filed stops
   matching its own address.

Files are fanned out two levels deep (``ab/cd/abcd….pdf``) because a single
directory holding a hospital-year of reports is a directory nobody can list.

**Writes are atomic.** The bytes go to a temporary file in the same directory
and are then ``os.replace``d onto the final name, which is atomic on POSIX
within a filesystem. A half-written PDF that happens to carry a valid-looking
name would be a document whose content-address lies about its content.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

CHUNK_BYTES = 1024 * 1024


class StorageError(RuntimeError):
    """The bytes could not be stored, or what is stored is not what was asked
    for."""


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    # Relative to the document root. The DB stores this, never an absolute
    # path, so moving the volume is a config change rather than an UPDATE over
    # every row.
    relative_path: str
    size_bytes: int
    # True when an identical file was already on disk. The caller uses this to
    # distinguish "new document" from "someone re-sent the same report", which
    # is a different thing to tell a user.
    deduplicated: bool


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path_for(sha256: str, suffix: str = ".pdf") -> str:
    """``ab/cd/<full hash><suffix>``.

    The full hash stays in the filename even though its first four characters
    are also the directory names — so a file that gets copied out of the tree
    still carries its own address.
    """
    if len(sha256) != 64:
        raise StorageError(f"not a sha256 digest: {sha256!r}")
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}{suffix}"


class DocumentStore:
    """The file side of ingestion. Knows nothing about the database."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def absolute(self, relative_path: str) -> Path:
        """Resolve a stored path, refusing anything that escapes the root.

        ``storage_path`` comes out of the database, and the database is
        written by this process — but an endpoint that serves a file named by
        a column is one SQL-injection away from serving ``/etc/shadow`` if it
        does not check. Cheap here, unrecoverable if omitted.
        """
        root = self.root.resolve()
        candidate = (root / relative_path).resolve()
        if candidate != root and root not in candidate.parents:
            raise StorageError(f"path escapes the document root: {relative_path!r}")
        return candidate

    def put_bytes(self, data: bytes, *, suffix: str = ".pdf") -> StoredFile:
        if not data:
            raise StorageError("refusing to store an empty file")

        digest = sha256_bytes(data)
        relative = relative_path_for(digest, suffix)
        target = self.absolute(relative)

        if target.exists():
            # Already filed. Do not rewrite it: the bytes are addressed by
            # their own hash, so the file that is there IS this file, and
            # rewriting only creates a window where it is not.
            log.info("document_deduplicated", sha256=digest)
            return StoredFile(
                sha256=digest,
                relative_path=relative,
                size_bytes=len(data),
                deduplicated=True,
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, data)
        log.info("document_stored", sha256=digest, size_bytes=len(data))
        return StoredFile(
            sha256=digest,
            relative_path=relative,
            size_bytes=len(data),
            deduplicated=False,
        )

    def put_file(self, source: Path, *, suffix: str | None = None) -> StoredFile:
        """Store a file already on disk — the watched-folder path.

        Read in chunks: the watched folder is how a lab drops a day's worth of
        reports at once, and holding a 25 MB file in memory per document is a
        needless way to make that fail.
        """
        digest = sha256_file(source)
        relative = relative_path_for(digest, suffix or source.suffix.lower() or ".pdf")
        target = self.absolute(relative)
        size = source.stat().st_size

        if target.exists():
            log.info("document_deduplicated", sha256=digest)
            return StoredFile(
                sha256=digest,
                relative_path=relative,
                size_bytes=size,
                deduplicated=True,
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as handle:
            self._atomic_write_stream(target, handle)
        log.info("document_stored", sha256=digest, size_bytes=size)
        return StoredFile(
            sha256=digest, relative_path=relative, size_bytes=size, deduplicated=False
        )

    def read(self, relative_path: str) -> bytes:
        path = self.absolute(relative_path)
        if not path.exists():
            raise StorageError(f"stored file is missing: {relative_path}")
        return path.read_bytes()

    def verify(self, relative_path: str, expected_sha256: str) -> bool:
        """Re-hash what is on disk. Used by the retry path before reprocessing.

        A document whose bytes no longer match its name is not a document to
        retry — it is an incident.
        """
        path = self.absolute(relative_path)
        if not path.exists():
            return False
        return sha256_file(path) == expected_sha256

    # ── page images (6.4) ─────────────────────────────────────────
    def page_image_path(self, sha256: str, page_no: int) -> str:
        """Where a rendered page PNG lives, relative to the root.

        Beside the document rather than in a parallel tree, so deleting a
        document's directory takes its renders with it.
        """
        return f"{sha256[:2]}/{sha256[2:4]}/{sha256}.pages/p{page_no:04d}.png"

    def put_page_image(self, sha256: str, page_no: int, png: bytes) -> str:
        relative = self.page_image_path(sha256, page_no)
        target = self.absolute(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, png)
        return relative

    # ── internals ─────────────────────────────────────────────────
    def _atomic_write(self, target: Path, data: bytes) -> None:
        handle, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
        try:
            with os.fdopen(handle, "wb") as tmp:
                tmp.write(data)
                tmp.flush()
                # fsync before the rename: os.replace is atomic with respect
                # to *other processes*, not with respect to a power cut. A
                # crash between write and flush leaves a correctly-named file
                # with missing bytes, which is the one failure this whole
                # module exists to prevent.
                os.fsync(tmp.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _atomic_write_stream(self, target: Path, source: object) -> None:
        read = getattr(source, "read", None)
        if read is None:  # pragma: no cover - programming error
            raise StorageError("source is not readable")
        handle, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
        try:
            with os.fdopen(handle, "wb") as tmp:
                while chunk := read(CHUNK_BYTES):
                    tmp.write(chunk)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise


def get_store(root: str | Path | None = None) -> DocumentStore:
    from app.config import get_settings

    return DocumentStore(root or get_settings().document_root)

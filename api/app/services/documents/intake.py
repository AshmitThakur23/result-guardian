"""Accepting a file. Phase 6.1.

    `POST /api/reports/upload` — multipart, **max 25 MB**, MIME sniffing
    (**do not trust the extension**)

The order of operations here is the security-relevant part, and it is:

1. **size** — cheapest check, and the one that stops a memory exhaustion
   attempt before anything else touches the bytes;
2. **MIME sniff** — from the content, never the filename;
3. **virus scan** — 6.1 says *before* processing, and "processing" includes
   writing the file where a scanner-less path might later read it;
4. **store** — content-addressed, atomic;
5. **row + enqueue** — in one transaction.

A file that fails any of 1–3 never reaches disk.

**Deduplication is an answer, not an error.** Re-sending the same report is
normal: a lab re-faxes, a clerk clicks twice, the watched folder re-scans an
archive. The second arrival returns the first document's id and enqueues
nothing. Treating it as a conflict would train people to work around it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import audit
from app.services.documents import repository, scan
from app.services.documents.storage import get_store

log = structlog.get_logger(__name__)

# What a lab actually sends. PDFs overwhelmingly, images when someone
# photographs a printout at a ward desk.
ACCEPTED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}

_SUFFIX_FOR_MIME = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tif",
}

# Magic numbers, for when libmagic is not available. Short and boring on
# purpose: this is a fallback, not a parser.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


class IntakeRejectedError(Exception):
    """The file was refused. ``reason`` is shown to the user verbatim, so it
    must say what to do about it."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Accepted:
    document_id: uuid.UUID
    sha256: str
    # True when this file was already in the system. The caller tells the user,
    # because "we already have this" is useful and "uploaded!" would be a lie.
    duplicate: bool
    size_bytes: int
    mime_type: str
    scan_verdict: str


def sniff_mime(data: bytes, filename: str | None = None) -> str:
    """Identify the content. **The extension is never consulted.**

    6.1 is explicit about this, and the reason is not theoretical: a hospital
    mail gateway that renames attachments, and an attacker who names a
    payload ``report.pdf``, produce the same wrong answer if you trust the
    name. ``filename`` is accepted only so it can be logged next to what the
    bytes actually were.
    """
    detected: str | None = None
    try:
        import magic

        detected = magic.from_buffer(data[:4096], mime=True)
    except Exception as exc:
        # libmagic missing or unhappy. Fall through to signatures rather than
        # refusing every upload -- but say so, because the fallback recognises
        # far less.
        log.info("libmagic_unavailable", error=str(exc))

    if not detected or detected == "application/octet-stream":
        for signature, mime in _SIGNATURES:
            if data.startswith(signature):
                detected = mime
                break

    resolved = detected or "application/octet-stream"
    if filename and not filename.lower().endswith(_SUFFIX_FOR_MIME.get(resolved, "\0")):
        # Not an error -- just worth knowing that the name disagreed with the
        # bytes, because that is what a gateway rename looks like in a log.
        log.info("mime_extension_mismatch", filename=filename, sniffed=resolved)
    return resolved


async def accept_upload(
    session: AsyncSession,
    *,
    data: bytes,
    filename: str | None,
    source_channel: str,
    uploaded_by: uuid.UUID | None,
    actor_ip: str | None = None,
    order_id: uuid.UUID | None = None,
) -> Accepted:
    """Run the intake checks and file the document. **Does not commit.**"""
    settings = get_settings()

    # 1. Size.
    if not data:
        raise IntakeRejectedError("The file is empty.")
    if len(data) > settings.upload_max_bytes:
        limit_mb = settings.upload_max_bytes // (1024 * 1024)
        raise IntakeRejectedError(
            f"The file is larger than the {limit_mb} MB limit. "
            "Split it, or scan at a lower resolution."
        )

    # 2. What it actually is.
    mime_type = sniff_mime(data, filename)
    if mime_type not in ACCEPTED_MIME_TYPES:
        raise IntakeRejectedError(
            f"This is a {mime_type} file. Upload a PDF, PNG, JPEG or TIFF."
        )

    # 3. Virus scan, before anything is written anywhere.
    verdict = await scan.scan_for_settings(data)
    if not verdict.may_process:
        log.error(
            "upload_refused_by_scanner",
            verdict=verdict.verdict.value,
            detail=verdict.detail,
        )
        raise IntakeRejectedError(
            "The file was not accepted by the virus scanner. " + verdict.detail
        )

    # 4. Store. Content-addressed, so this is also the dedup key.
    store = get_store()
    stored = store.put_bytes(data, suffix=_SUFFIX_FOR_MIME.get(mime_type, ".bin"))

    existing = await repository.find_by_sha256(session, stored.sha256)
    if existing is not None:
        log.info(
            "upload_deduplicated",
            document_id=str(existing.id),
            sha256=stored.sha256,
        )
        return Accepted(
            document_id=existing.id,
            sha256=stored.sha256,
            duplicate=True,
            size_bytes=stored.size_bytes,
            mime_type=existing.mime_type,
            scan_verdict=verdict.verdict.value,
        )

    # 5. Row and queue message, in the caller's transaction.
    document_id = await repository.insert(
        session,
        sha256=stored.sha256,
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=stored.size_bytes,
        storage_path=stored.relative_path,
        source_channel=source_channel,
        uploaded_by=uploaded_by,
        order_id=order_id,
    )
    await repository.enqueue_ingest(session, document_id)
    await audit.append(
        session,
        action=audit.ACTION_DOCUMENT_UPLOADED,
        entity_type="document",
        entity_id=document_id,
        actor_user_id=uploaded_by,
        actor_ip=actor_ip,
        after={
            "sha256": stored.sha256,
            "original_filename": filename,
            "mime_type": mime_type,
            "size_bytes": stored.size_bytes,
            "source_channel": source_channel,
            # Recorded because "no scanner was deployed" is a fact about this
            # document that somebody may need to establish later.
            "virus_scan": verdict.verdict.value,
        },
    )

    log.info(
        "document_accepted",
        document_id=str(document_id),
        sha256=stored.sha256,
        source_channel=source_channel,
        size_bytes=stored.size_bytes,
    )
    return Accepted(
        document_id=document_id,
        sha256=stored.sha256,
        duplicate=False,
        size_bytes=stored.size_bytes,
        mime_type=mime_type,
        scan_verdict=verdict.verdict.value,
    )

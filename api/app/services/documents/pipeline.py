"""The ingestion pipeline. Phase 6.5.

    - pgmq `ingest` queue → worker → status transitions written **before and
      after** each stage
    - Timeout per document (**default 120s**), then fail cleanly
    - DLQ + admin retry button
    - **Fallback is mandatory:** any failure routes the document to the Phase 3
      manual entry form with the page images shown side-by-side. **The workflow
      never stalls because parsing failed.**

That last paragraph is the phase's safety property, and it is the reason this
module has so few ways to raise. THE ONE RULE — *a later phase must never be
able to break an earlier one* — has a specific meaning here: before Phase 6, a
clerk typed results into the Phase 3 form and the system worked. Phase 6 must
therefore degrade **to that form**, never to silence. Every failure path below
ends with a document a human can open, with its pages rendered beside the
entry fields, because a document that fails invisibly is worse than no
ingestion at all: the ward believes the result arrived.

So the outcomes are:

* **``extracted``** — text and spans are on file. Phase 7 can take it.
* **``needs_review``** — we read something, or nothing, and do not trust it.
  Page images rendered, fallback available.
* **``failed``** — we could not read the file at all. Page images rendered if
  the file could be opened even partially; fallback available regardless.

There is deliberately no fourth outcome where the document simply stops.

★ **Extraction runs in a separate process, and that is a safety requirement,
not a performance one.**

It began as ``asyncio.to_thread``, and a real scanned page proved that wrong
in the worst available way: PaddleOCR on an 11-megapixel render exceeded the
container's memory limit and the Linux OOM killer **SIGKILLed the whole worker
process**. That process also runs the SLA-timer consumer and the notification
consumer — so one unreadable PDF stopped Phase 2's escalation and Phase 4's
messaging, every sixty seconds, while pgmq faithfully redelivered it.

A thread cannot be protected from that. A thread shares the process, so
anything that kills the interpreter kills the timers with it, and nothing
inside Python can intervene. A child process can: if it dies, the parent gets
``BrokenProcessPool``, marks the document ``failed``, and the timers never
notice.

It also makes the timeout real. ``asyncio.wait_for`` around a thread bounds
only the *wait*; the thread runs on regardless, because Python cannot
interrupt a C extension mid-call and PyMuPDF and PaddleOCR are both C
extensions. Around a process, the pool can be torn down and the work actually
stops.

The cost is one process spawn and one model load per pool, amortised across
every document that follows. That is a fair price for "a bad PDF cannot stop
the escalation ladder".
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services import audit
from app.services.documents import detect, native, ocr, render, repository
from app.services.documents import settings as ingest_settings
from app.services.documents.storage import DocumentStore, get_store

log = structlog.get_logger(__name__)

# One worker: extraction is already CPU-saturating and a second concurrent
# OCR would double peak memory for no throughput on the cores available.
_pool: concurrent.futures.ProcessPoolExecutor | None = None


def _get_pool() -> concurrent.futures.ProcessPoolExecutor:
    """The extraction pool, created on first use.

    Lazy because spawning a child and loading OCR models costs seconds, and a
    deployment that never receives a document should never pay it.
    """
    global _pool
    if _pool is None:
        _pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    return _pool


def shutdown_pool() -> None:
    """Tear the pool down. Called after a timeout or a child death, and by
    tests.

    ``cancel_futures=True`` and no wait: the whole point is that we are no
    longer willing to wait for whatever that child is doing.
    """
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


@dataclass
class PageOutcome:
    page_no: int
    width_pt: float
    height_pt: float
    is_scanned: bool
    text_layer: str
    ocr_confidence: float | None
    image_path: str | None
    spans: list[native.Span] = field(default_factory=list)
    # Why this page is not trustworthy, if it is not. Carried to the review
    # queue so the human sees a reason, not just a flag.
    review_reason: str | None = None


@dataclass
class IngestOutcome:
    document_id: uuid.UUID
    status: str
    pages: list[PageOutcome]
    error_text: str | None = None

    @property
    def needs_human(self) -> bool:
        return self.status in repository.UNRESOLVED_STATUSES


class DocumentMissingError(Exception):
    """The document row is gone. Not retryable, and not an error worth a DLQ
    hop."""


async def ingest_document(
    session: AsyncSession, document_id: uuid.UUID
) -> IngestOutcome:
    """Process one document end to end. **Commits its own status transitions.**

    Unlike most services here, this one commits — twice. 6.5 requires the
    status to be written *before* the stage as well as after, and a "before"
    written in an uncommitted transaction is invisible to the dashboard that
    needs to show the document as in-flight. The alternative is a document
    that appears to sit in ``received`` for two minutes and then jumps to
    ``extracted``, with no way to tell a slow page from a stuck worker.
    """
    document = await repository.get(session, document_id)
    if document is None:
        raise DocumentMissingError(str(document_id))

    tuning = await ingest_settings.load(session)
    store = get_store()

    # ── before the stage ──────────────────────────────────────────
    await repository.set_status(
        session,
        document_id,
        repository.STATUS_EXTRACTING,
        started=True,
        increment_attempts=True,
    )
    await session.commit()

    loop = asyncio.get_running_loop()
    try:
        outcome = await asyncio.wait_for(
            loop.run_in_executor(
                _get_pool(),
                _extract_in_child,
                store.root,
                document.storage_path,
                document.sha256,
                document_id,
                tuning.scanned_char_density,
                tuning.ocr_min_confidence,
                get_settings().page_render_dpi,
            ),
            timeout=tuning.ingest_timeout_s,
        )
    except TimeoutError:
        # 6.5: "then fail cleanly". Cleanly means the document lands somewhere
        # a human will look, not that the worker logs and shrugs.
        #
        # Tear the pool down rather than leaving the child grinding away: a
        # timed-out document that keeps a core busy makes every document
        # behind it slower, and the next one gets a fresh child.
        shutdown_pool()
        message = (
            f"Extraction did not finish within {tuning.ingest_timeout_s:.0f} "
            "seconds. Enter this result manually from the page images."
        )
        log.warning("ingest_timeout", document_id=str(document_id))
        return await _finish(
            session,
            document_id,
            repository.STATUS_FAILED,
            pages=[],
            error_text=message,
        )
    except concurrent.futures.process.BrokenProcessPool:
        # ★ The child died — OOM-killed, or a native library aborted. Before
        # extraction was moved into its own process this killed the **worker**,
        # taking the SLA-timer and notification consumers with it. Now it
        # costs exactly one document, and that document reaches a human.
        shutdown_pool()
        log.error("ingest_child_died", document_id=str(document_id))
        return await _finish(
            session,
            document_id,
            repository.STATUS_FAILED,
            pages=[],
            error_text=(
                "Reading this document used more memory than the server "
                "allows, so it was stopped. Enter the result manually from "
                "the report."
            ),
        )
    except Exception as exc:
        # Anything unforeseen. The document still has to reach a human.
        log.exception("ingest_failed", document_id=str(document_id))
        return await _finish(
            session,
            document_id,
            repository.STATUS_FAILED,
            pages=[],
            error_text=(
                f"Extraction failed: {exc}. Enter this result manually from "
                "the page images."
            ),
        )

    return await _finish(
        session,
        document_id,
        outcome.status,
        pages=outcome.pages,
        error_text=outcome.error_text,
    )


async def _finish(
    session: AsyncSession,
    document_id: uuid.UUID,
    status: str,
    *,
    pages: list[PageOutcome],
    error_text: str | None,
) -> IngestOutcome:
    """Write pages, spans, status and the audit row in one transaction.

    One transaction on purpose: a document whose status says ``extracted``
    while its spans belong to the previous attempt is a document whose
    citations point at the wrong page.
    """
    await repository.replace_pages(
        session,
        document_id,
        [
            {
                "page_no": page.page_no,
                "width_pt": page.width_pt,
                "height_pt": page.height_pt,
                "is_scanned": page.is_scanned,
                "text_layer": page.text_layer,
                # The CHECK constraint enforces this too, but a value that
                # never leaves the process is better than one the database has
                # to reject: a native page has no OCR confidence.
                "ocr_confidence": page.ocr_confidence if page.is_scanned else None,
                "image_path": page.image_path,
            }
            for page in pages
        ],
    )
    spans: list[native.Span] = []
    for page in pages:
        spans.extend(page.spans)
    await repository.replace_spans(session, document_id, spans)

    await repository.set_status(
        session,
        document_id,
        status,
        error_text=error_text,
        page_count=len(pages) or None,
        ended=True,
    )
    await audit.append(
        session,
        action=(
            audit.ACTION_DOCUMENT_INGESTED
            if status == repository.STATUS_EXTRACTED
            else audit.ACTION_DOCUMENT_FAILED
        ),
        entity_type="document",
        entity_id=document_id,
        after={
            "status": status,
            "page_count": len(pages),
            "span_count": len(spans),
            "scanned_pages": sum(1 for p in pages if p.is_scanned),
            "error_text": error_text,
        },
    )
    await session.commit()

    log.info(
        "ingest_finished",
        document_id=str(document_id),
        status=status,
        pages=len(pages),
        spans=len(spans),
    )
    return IngestOutcome(
        document_id=document_id, status=status, pages=pages, error_text=error_text
    )


def _extract_in_child(
    document_root: Path,
    storage_path: str,
    sha256: str,
    document_id: uuid.UUID,
    char_density_threshold: float,
    ocr_min_confidence: float,
    dpi: int,
) -> IngestOutcome:
    """The child process's entry point.

    Positional, plain arguments on purpose: everything crossing a process
    boundary is pickled, so this signature takes a ``Path`` and primitives
    rather than a live ``DocumentStore``. The store is rebuilt on the other
    side — it is only a root path and some methods.

    Keeping this wrapper separate from :func:`_extract_blocking` means the
    real work stays directly callable in tests, with no pool involved.
    """
    return _extract_blocking(
        store=DocumentStore(document_root),
        storage_path=storage_path,
        sha256=sha256,
        document_id=document_id,
        char_density_threshold=char_density_threshold,
        ocr_min_confidence=ocr_min_confidence,
        dpi=dpi,
    )


def _extract_blocking(
    *,
    store: DocumentStore,
    storage_path: str,
    sha256: str,
    document_id: uuid.UUID,
    char_density_threshold: float,
    ocr_min_confidence: float,
    dpi: int,
) -> IngestOutcome:
    """All the CPU work, off the event loop. Returns, never raises for a bad
    document."""
    path = store.absolute(storage_path)

    if not path.exists():
        return IngestOutcome(
            document_id=document_id,
            status=repository.STATUS_FAILED,
            pages=[],
            error_text=("The stored file is missing from disk. Re-upload the report."),
        )

    # The file is named after its own hash. If it no longer hashes to its name,
    # something changed it after it was filed -- do not parse it and do not
    # pretend it is the document that was uploaded.
    if not store.verify(storage_path, sha256):
        return IngestOutcome(
            document_id=document_id,
            status=repository.STATUS_FAILED,
            pages=[],
            error_text=(
                "The stored file no longer matches its content hash. It may be "
                "corrupted. Re-upload the report."
            ),
        )

    inspection = detect.inspect(path, char_density_threshold=char_density_threshold)

    if not inspection.ok:
        # 6.2's two named cases. Encrypted goes to review (a human may have the
        # password); corrupt and empty are failures. Both get page images if
        # any can be produced, because the fallback needs something to read.
        status = (
            repository.STATUS_NEEDS_REVIEW
            if inspection.failure is detect.OpenFailure.ENCRYPTED
            else repository.STATUS_FAILED
        )
        return IngestOutcome(
            document_id=document_id,
            status=status,
            pages=[],
            error_text=_human_message(inspection),
        )

    pages = _process_pages(
        path=path,
        store=store,
        sha256=sha256,
        inspection=inspection,
        dpi=dpi,
        ocr_min_confidence=ocr_min_confidence,
    )

    review_reasons = [p.review_reason for p in pages if p.review_reason]
    any_text = any(p.text_layer.strip() for p in pages)

    if not any_text:
        # Nothing readable came out. Not a crash -- a document that has to be
        # typed, which is exactly the pre-Phase-6 workflow.
        return IngestOutcome(
            document_id=document_id,
            status=repository.STATUS_NEEDS_REVIEW,
            pages=pages,
            error_text=(
                "No readable text could be extracted from this document. "
                "Enter the result manually from the page images."
            ),
        )

    if review_reasons:
        return IngestOutcome(
            document_id=document_id,
            status=repository.STATUS_NEEDS_REVIEW,
            pages=pages,
            error_text="; ".join(dict.fromkeys(review_reasons)),
        )

    return IngestOutcome(
        document_id=document_id,
        status=repository.STATUS_EXTRACTED,
        pages=pages,
        error_text=None,
    )


def _human_message(inspection: detect.Inspection) -> str:
    """Turn an open failure into something a ward clerk can act on.

    *"else fail with a clear error"* — 6.2. A clear error says what to do
    next, not what the parser thought.
    """
    if inspection.failure is detect.OpenFailure.ENCRYPTED:
        return (
            "This PDF is password-protected. Ask the lab to send an unlocked "
            "copy, or enter the result manually."
        )
    if inspection.failure is detect.OpenFailure.EMPTY:
        return "This PDF has no pages. Ask the lab to re-send the report."
    return (
        "This file could not be read as a PDF, even after a repair attempt. "
        f"Ask the lab to re-send it, or enter the result manually. "
        f"({inspection.detail})"
    )


def _process_pages(
    *,
    path: Path,
    store: DocumentStore,
    sha256: str,
    inspection: detect.Inspection,
    dpi: int,
    ocr_min_confidence: float,
) -> list[PageOutcome]:
    """Walk the pages once, sending each down the path 6.2 chose for it."""
    import pymupdf

    outcomes: list[PageOutcome] = []
    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass:
            doc.authenticate("")

        for kind in inspection.pages:
            page = doc.load_page(kind.page_no - 1)

            if not kind.is_scanned:
                # 6.3. The text layer is already there and is better than
                # anything OCR would produce from a render of it.
                extracted = native.extract_page(page, kind.page_no)
                outcomes.append(
                    PageOutcome(
                        page_no=kind.page_no,
                        width_pt=kind.width_pt,
                        height_pt=kind.height_pt,
                        is_scanned=False,
                        text_layer=extracted.text,
                        ocr_confidence=None,
                        image_path=None,
                        spans=extracted.spans,
                    )
                )
                continue

            # 6.4. Render first: the image is needed for OCR *and* for the
            # fallback, so it is produced even when OCR then fails.
            image_path: str | None = None
            rendered: render.RenderedPage | None = None
            try:
                rendered = render.render_page(page, kind.page_no, dpi=dpi)
                image_path = store.put_page_image(sha256, kind.page_no, rendered.png)
            except Exception as exc:
                log.warning("page_render_failed", page_no=kind.page_no, error=str(exc))

            if rendered is None:
                outcomes.append(
                    PageOutcome(
                        page_no=kind.page_no,
                        width_pt=kind.width_pt,
                        height_pt=kind.height_pt,
                        is_scanned=True,
                        text_layer="",
                        ocr_confidence=None,
                        image_path=None,
                        review_reason=(
                            f"Page {kind.page_no} could not be rendered for " "reading."
                        ),
                    )
                )
                continue

            outcomes.append(
                _ocr_one_page(
                    rendered=rendered,
                    kind=kind,
                    image_path=image_path,
                    dpi=dpi,
                    ocr_min_confidence=ocr_min_confidence,
                )
            )

        # Native pages get an image too, but only if some page in the document
        # needs human attention — the fallback form shows every page, not just
        # the broken ones, and a clerk reading page 2 needs page 1 for the
        # patient's name. Rendering every native page unconditionally would
        # triple ingestion time for documents nobody will ever look at.
        if any(o.review_reason for o in outcomes) or not any(
            o.text_layer.strip() for o in outcomes
        ):
            _render_missing_images(
                doc=doc, store=store, sha256=sha256, outcomes=outcomes, dpi=dpi
            )

    return outcomes


def _ocr_one_page(
    *,
    rendered: render.RenderedPage,
    kind: detect.PageKind,
    image_path: str | None,
    dpi: int,
    ocr_min_confidence: float,
) -> PageOutcome:
    try:
        image = render.png_to_array(rendered.png)
    except Exception as exc:
        return PageOutcome(
            page_no=kind.page_no,
            width_pt=kind.width_pt,
            height_pt=kind.height_pt,
            is_scanned=True,
            text_layer="",
            ocr_confidence=None,
            image_path=image_path,
            review_reason=f"Page {kind.page_no} image could not be decoded: {exc}",
        )

    result = ocr.ocr_page(
        image,
        kind.page_no,
        dpi=dpi,
        scale_to_pdf=rendered.points_per_pixel,
    )

    if result.unavailable_reason:
        # The engine is missing or broke. The page image is stored, so the
        # clerk has everything they had before Phase 6 existed.
        return PageOutcome(
            page_no=kind.page_no,
            width_pt=kind.width_pt,
            height_pt=kind.height_pt,
            is_scanned=True,
            text_layer="",
            ocr_confidence=None,
            image_path=image_path,
            review_reason=(
                f"Page {kind.page_no} could not be read automatically "
                f"({result.unavailable_reason}). Enter it from the image."
            ),
        )

    reason: str | None = None
    if result.mean_confidence is None:
        if not result.text.strip():
            reason = (
                f"Page {kind.page_no} appears to be blank or unreadable. "
                "Check the image."
            )
    elif result.mean_confidence < ocr_min_confidence:
        # 6.4, verbatim in intent: route to needs_review INSTEAD OF GUESSING.
        # The recognised text is still stored -- it gives the clerk somewhere
        # to start -- but the document does not proceed on it.
        reason = (
            f"Page {kind.page_no} was read with low confidence "
            f"({result.mean_confidence:.0%}, below the "
            f"{ocr_min_confidence:.0%} threshold). Check every value against "
            "the image."
        )

    return PageOutcome(
        page_no=kind.page_no,
        width_pt=kind.width_pt,
        height_pt=kind.height_pt,
        is_scanned=True,
        text_layer=result.text,
        ocr_confidence=result.mean_confidence,
        image_path=image_path,
        spans=result.spans,
        review_reason=reason,
    )


def _render_missing_images(
    *,
    doc: Any,
    store: DocumentStore,
    sha256: str,
    outcomes: list[PageOutcome],
    dpi: int,
) -> None:
    """Give every page an image, for the side-by-side fallback view."""
    for outcome in outcomes:
        if outcome.image_path:
            continue
        try:
            page = doc.load_page(outcome.page_no - 1)
            rendered = render.render_page(page, outcome.page_no, dpi=dpi)
            outcome.image_path = store.put_page_image(
                sha256, outcome.page_no, rendered.png
            )
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "fallback_render_failed", page_no=outcome.page_no, error=str(exc)
            )

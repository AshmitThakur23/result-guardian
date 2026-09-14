"""Phase 6.5 — the pipeline, its failure routing, and the mandatory fallback.

    **Fallback is mandatory:** any failure routes the document to the Phase 3
    manual entry form with the page images shown side-by-side. **The workflow
    never stalls because parsing failed.**

That sentence is the phase's safety property and most of this file tests it.
THE ONE RULE has a specific meaning for Phase 6: before it existed, a clerk
typed results into the Phase 3 form and the system worked. Phase 6 must
degrade **to that form**, never to silence.

These tests need Postgres. The `session` fixture in conftest.py skips them
when none is reachable.
"""

from __future__ import annotations

import concurrent.futures
import os
import uuid

import pytest

from app.services.documents import intake, pipeline, repository
from app.services.documents.storage import DocumentStore
from tests import _documents

pytestmark = pytest.mark.asyncio


async def _ingest(session, monkeypatch, tmp_path, data: bytes, name="r.pdf"):
    """Put a file through intake and then the pipeline, against a temp store."""
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    monkeypatch.setattr(
        "app.services.documents.pipeline.get_store", lambda *a, **k: store
    )

    accepted = await intake.accept_upload(
        session,
        data=data,
        filename=name,
        source_channel="upload",
        uploaded_by=None,
    )
    await session.commit()
    outcome = await pipeline.ingest_document(session, accepted.document_id)
    return accepted, outcome, store


# ── the happy path ────────────────────────────────────────────────
async def test_a_native_pdf_extracts_with_text_and_spans(
    session, monkeypatch, tmp_path
):
    _, outcome, _ = await _ingest(
        session, monkeypatch, tmp_path, _documents.native_pdf(pages=2)
    )

    assert outcome.status == repository.STATUS_EXTRACTED
    assert len(outcome.pages) == 2
    assert all(page.text_layer.strip() for page in outcome.pages)
    assert all(page.spans for page in outcome.pages)
    assert outcome.needs_human is False


async def test_spans_survive_the_round_trip_through_the_database(
    session, monkeypatch, tmp_path
):
    """★ The invariant, checked after storage rather than in memory.

    ``text_layer[char_start:char_end] == text`` must hold for what comes back
    out of Postgres, not merely for what went in. A JSONB round-trip, a
    NUMERIC coercion or an encoding difference would break Phase 8's verifier
    just as thoroughly as a bad offset.
    """
    accepted, _, _ = await _ingest(
        session, monkeypatch, tmp_path, _documents.native_pdf()
    )

    rows = await repository.pages_for(session, accepted.document_id)
    pages = {p.page_no: p for p in rows}
    spans = await repository.spans_for(session, accepted.document_id)

    assert spans
    for span in spans:
        layer = pages[span.page_no].text_layer
        assert layer[span.char_start : span.char_end] == span.text
        assert set(span.bbox) == {"x0", "y0", "x1", "y1"}


async def test_page_count_and_status_are_written(session, monkeypatch, tmp_path):
    accepted, _, _ = await _ingest(
        session, monkeypatch, tmp_path, _documents.native_pdf(pages=3)
    )

    document = await repository.get(session, accepted.document_id)

    assert document is not None
    assert document.page_count == 3
    assert document.status == repository.STATUS_EXTRACTED
    assert document.error_text is None
    # 6.5: status written before AND after -- `attempts` is incremented by the
    # "before" write, so a document that reached a terminal status has at
    # least one recorded attempt.
    assert document.attempts >= 1


# ── the mandatory fallback ────────────────────────────────────────
async def test_an_encrypted_pdf_lands_in_the_review_queue(
    session, monkeypatch, tmp_path
):
    """6.2 routes it, 6.5 makes sure a human actually sees it."""
    accepted, outcome, _ = await _ingest(
        session, monkeypatch, tmp_path, _documents.encrypted_pdf(), "locked.pdf"
    )

    assert outcome.status == repository.STATUS_NEEDS_REVIEW
    assert outcome.needs_human is True

    queue = await repository.review_queue(session)
    assert accepted.document_id in {row.id for row in queue}


async def test_an_unreadable_file_fails_with_an_actionable_message(
    session, monkeypatch, tmp_path
):
    """*"else fail with a clear error"* — clear meaning it says what to do
    next, not what the parser thought."""
    _, outcome, _ = await _ingest(
        session, monkeypatch, tmp_path, b"%PDF-1.4 " + b"garbage" * 500, "junk.pdf"
    )

    assert outcome.status in repository.UNRESOLVED_STATUSES
    assert outcome.error_text
    # Actionable: it tells the reader what their next move is.
    assert "manually" in outcome.error_text or "re-send" in outcome.error_text


async def test_a_document_that_never_finishes_still_reaches_a_human(
    session, monkeypatch, tmp_path
):
    """6.5: *"Timeout per document (default 120s), then fail cleanly."*

    Cleanly means the document lands somewhere a human will look — not that
    the worker logs and shrugs.
    """
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    monkeypatch.setattr(
        "app.services.documents.pipeline.get_store", lambda *a, **k: store
    )
    accepted = await intake.accept_upload(
        session,
        data=_documents.native_pdf(),
        filename="slow.pdf",
        source_channel="upload",
        uploaded_by=None,
    )
    await session.commit()

    # Exercise the REAL timeout rather than stubbing `asyncio.wait_for`: move
    # the configured budget down to 100ms and make extraction take longer than
    # that. Stubbing wait_for would prove only that the `except TimeoutError`
    # branch exists, not that the timeout it depends on actually fires.
    import time

    from app.services.documents import settings as ingest_settings

    real_load = ingest_settings.load

    async def impatient(session_):
        tuning = await real_load(session_)
        return ingest_settings.IngestSettings(
            ocr_min_confidence=tuning.ocr_min_confidence,
            scanned_char_density=tuning.scanned_char_density,
            ingest_timeout_s=0.1,
        )

    monkeypatch.setattr(
        "app.services.documents.pipeline.ingest_settings.load", impatient
    )
    # Extraction runs in a child process, so a patch applied here would not
    # reach it through a pickled function reference. Swapping the pool for a
    # thread pool keeps the work in-process, which is what lets the patch
    # apply — and still exercises the real `run_in_executor` + `wait_for`
    # path, which is the thing under test.
    monkeypatch.setattr(
        "app.services.documents.pipeline._get_pool",
        lambda: concurrent.futures.ThreadPoolExecutor(max_workers=1),
    )
    monkeypatch.setattr(
        "app.services.documents.pipeline._extract_in_child",
        lambda *_args: time.sleep(1.5),
    )

    outcome = await pipeline.ingest_document(session, accepted.document_id)

    assert outcome.status == repository.STATUS_FAILED
    assert outcome.needs_human is True
    assert "manually" in (outcome.error_text or "")
    queue = await repository.review_queue(session)
    assert accepted.document_id in {row.id for row in queue}


def _suicidal_child(*_args):
    """Die the way an OOM kill dies: no exception, no unwinding, just gone.

    Module level because a ``ProcessPoolExecutor`` resolves the submitted
    function by qualified name.
    """
    os._exit(1)


async def test_a_child_that_is_killed_does_not_take_the_worker_with_it(
    session, monkeypatch, tmp_path
):
    """★★ THE ONE RULE, at the point where Phase 6 nearly broke Phase 2.

    This is the defect that made extraction a separate process. PaddleOCR on
    an 11-megapixel render exceeded the container's memory limit and the OOM
    killer **SIGKILLed the whole worker** — the process that also runs the
    SLA-timer and notification consumers. One unreadable PDF was stopping
    Phase 2's escalation and Phase 4's messaging every sixty seconds, while
    pgmq faithfully redelivered it.

    A thread could never have survived that. A child process can: the parent
    gets ``BrokenProcessPool``, marks the document failed, and the timers
    never notice.

    ``os._exit(1)`` is the honest simulation — no exception, no cleanup, the
    process simply stops, exactly as SIGKILL leaves it.
    """
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    monkeypatch.setattr(
        "app.services.documents.pipeline.get_store", lambda *a, **k: store
    )
    accepted = await intake.accept_upload(
        session,
        data=_documents.native_pdf(),
        filename="oom.pdf",
        source_channel="upload",
        uploaded_by=None,
    )
    await session.commit()

    # Drop any existing pool first: on Linux the child is forked, so it must
    # be forked *after* the patch for the patch to be present in it.
    pipeline.shutdown_pool()
    monkeypatch.setattr(
        "app.services.documents.pipeline._extract_in_child", _suicidal_child
    )

    try:
        outcome = await pipeline.ingest_document(session, accepted.document_id)
    finally:
        pipeline.shutdown_pool()

    assert outcome.status == repository.STATUS_FAILED
    assert outcome.needs_human is True
    assert "memory" in (outcome.error_text or "").lower()

    # And the document is in the queue a human actually reads.
    queue = await repository.review_queue(session)
    assert accepted.document_id in {row.id for row in queue}

    # The parent is still alive and still usable — which is the whole point.
    # A fresh document must extract normally straight afterwards.
    monkeypatch.undo()
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    monkeypatch.setattr(
        "app.services.documents.pipeline.get_store", lambda *a, **k: store
    )
    pipeline.shutdown_pool()
    healthy = await intake.accept_upload(
        session,
        data=_documents.native_pdf(text="Potassium 5.4 mmol/L"),
        filename="fine.pdf",
        source_channel="upload",
        uploaded_by=None,
    )
    await session.commit()
    recovered = await pipeline.ingest_document(session, healthy.document_id)
    pipeline.shutdown_pool()

    assert recovered.status == repository.STATUS_EXTRACTED, (
        "the pool must recover after a child death, or one bad document "
        "poisons every document after it"
    )


async def test_a_crash_inside_extraction_does_not_stall_the_document(
    session, monkeypatch, tmp_path
):
    """The unforeseen case. An exception anywhere in extraction still has to
    end with a document a human can open."""
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    monkeypatch.setattr(
        "app.services.documents.pipeline.get_store", lambda *a, **k: store
    )
    accepted = await intake.accept_upload(
        session,
        data=_documents.native_pdf(),
        filename="boom.pdf",
        source_channel="upload",
        uploaded_by=None,
    )
    await session.commit()

    def explode(*_args):
        raise RuntimeError("PyMuPDF segfaulted, as it were")

    monkeypatch.setattr(
        "app.services.documents.pipeline._get_pool",
        lambda: concurrent.futures.ThreadPoolExecutor(max_workers=1),
    )
    monkeypatch.setattr("app.services.documents.pipeline._extract_in_child", explode)

    outcome = await pipeline.ingest_document(session, accepted.document_id)

    assert outcome.status == repository.STATUS_FAILED
    document = await repository.get(session, accepted.document_id)
    assert document is not None
    assert document.status == repository.STATUS_FAILED
    assert (
        document.status != repository.STATUS_EXTRACTING
    ), "a document must never be left claiming to be in flight"


async def test_a_scanned_page_gets_an_image_even_when_ocr_is_unavailable(
    session, monkeypatch, tmp_path
):
    """★ **The fallback's load-bearing detail.**

    OCR being absent must produce a page the clerk can *read from*. A review
    queue entry with no image is not a fallback — it is a dead end with a text
    box, and the ward is still waiting for the result.
    """
    from app.services.documents import ocr

    ocr.reset_engine()
    monkeypatch.setattr(
        ocr,
        "_get_engine",
        lambda _lang: (_ for _ in ()).throw(
            ocr.OcrUnavailableError("PaddleOCR is not installed")
        ),
    )

    accepted, outcome, store = await _ingest(
        session, monkeypatch, tmp_path, _documents.scanned_pdf(), "scan.pdf"
    )

    assert outcome.status == repository.STATUS_NEEDS_REVIEW
    pages = await repository.pages_for(session, accepted.document_id)
    assert pages
    for page in pages:
        assert page.is_scanned is True
        assert page.image_path, "every scanned page must have a rendered image"
        # And the image is really on disk, not merely named in a column.
        assert store.absolute(page.image_path).exists()


async def test_ocr_absence_degrades_and_never_raises(monkeypatch):
    """THE ONE RULE at the module boundary.

    Phase 6 introduces a heavy native dependency into a system that had none.
    If it is missing, the cost must be accuracy, never availability.
    """
    import numpy as np

    from app.services.documents import ocr

    ocr.reset_engine()
    monkeypatch.setattr(
        ocr,
        "_get_engine",
        lambda _lang: (_ for _ in ()).throw(ocr.OcrUnavailableError("absent")),
    )

    result = ocr.ocr_page(np.zeros((50, 50, 3), dtype=np.uint8), 1, dpi=200)

    assert result.unavailable_reason == "absent"
    assert result.usable is False
    assert result.text == ""


# ── idempotency ───────────────────────────────────────────────────
async def test_processing_the_same_document_twice_leaves_one_set_of_pages(
    session, monkeypatch, tmp_path
):
    """pgmq is at-least-once, so this handler will run twice on the same
    document. `replace_pages` and `replace_spans` are what make that safe."""
    accepted, _, _ = await _ingest(
        session, monkeypatch, tmp_path, _documents.native_pdf(pages=2)
    )
    first_pages = await repository.pages_for(session, accepted.document_id)
    first_spans = await repository.spans_for(session, accepted.document_id)

    await pipeline.ingest_document(session, accepted.document_id)

    second_pages = await repository.pages_for(session, accepted.document_id)
    second_spans = await repository.spans_for(session, accepted.document_id)

    assert len(second_pages) == len(first_pages) == 2
    assert len(second_spans) == len(first_spans)


async def test_uploading_the_same_report_twice_creates_one_document(
    session, monkeypatch, tmp_path
):
    """Re-sending is normal: a lab re-faxes, a clerk clicks twice. Treating it
    as a conflict would train people to work around it."""
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    data = _documents.native_pdf()

    first = await intake.accept_upload(
        session,
        data=data,
        filename="a.pdf",
        source_channel="upload",
        uploaded_by=None,
    )
    second = await intake.accept_upload(
        session,
        data=data,
        filename="a-copy.pdf",
        source_channel="upload",
        uploaded_by=None,
    )

    assert first.document_id == second.document_id
    assert first.duplicate is False
    assert second.duplicate is True


async def test_a_missing_document_row_is_not_retried_into_the_dlq(session):
    with pytest.raises(pipeline.DocumentMissingError):
        await pipeline.ingest_document(session, uuid.uuid4())


# ── intake refusals ───────────────────────────────────────────────
async def test_an_oversized_file_is_refused_before_it_reaches_disk(
    session, monkeypatch, tmp_path
):
    from app.config import get_settings

    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )
    oversized = b"%PDF-1.4" + b"\0" * (get_settings().upload_max_bytes + 1)

    with pytest.raises(intake.IntakeRejectedError, match="larger than"):
        await intake.accept_upload(
            session,
            data=oversized,
            filename="huge.pdf",
            source_channel="upload",
            uploaded_by=None,
        )

    assert list(tmp_path.rglob("*.pdf")) == []


async def test_a_renamed_png_is_refused_as_a_pdf_but_accepted_as_an_image(
    session, monkeypatch, tmp_path
):
    """The sniffer decides the type; the name is only logged.

    A PNG named `.pdf` is still a perfectly good report photograph, so it is
    accepted — as an image. What must not happen is it being recorded as a
    PDF and handed to a PDF parser.
    """
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )

    accepted = await intake.accept_upload(
        session,
        data=_documents.not_a_pdf(),
        filename="urgent-result.pdf",
        source_channel="upload",
        uploaded_by=None,
    )

    assert accepted.mime_type == "image/png"


async def test_a_file_type_we_cannot_read_is_refused(session, monkeypatch, tmp_path):
    store = DocumentStore(tmp_path)
    monkeypatch.setattr(
        "app.services.documents.intake.get_store", lambda *a, **k: store
    )

    with pytest.raises(intake.IntakeRejectedError):
        await intake.accept_upload(
            session,
            data=b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 512,
            filename="report.pdf",
            source_channel="upload",
            uploaded_by=None,
        )


# ── recovery ──────────────────────────────────────────────────────
async def test_a_document_abandoned_mid_extraction_is_recovered(
    session, monkeypatch, tmp_path
):
    """A worker killed mid-extraction leaves a row saying `extracting` for
    ever — busy-looking, actually abandoned, and invisible to the review
    queue because `extracting` is not in it."""
    accepted, _, _ = await _ingest(
        session, monkeypatch, tmp_path, _documents.native_pdf()
    )
    # Put it back into the stuck state, started well in the past.
    await repository.set_status(
        session, accepted.document_id, repository.STATUS_EXTRACTING
    )
    from sqlalchemy import text as sql

    await session.execute(
        sql(
            "UPDATE documents SET processing_started_at = now() - interval '2 hours'"
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(accepted.document_id)},
    )

    stale = await repository.stale_processing(session, older_than_seconds=360.0)

    assert accepted.document_id in {row.id for row in stale}

"""Phase 6 over HTTP — upload, the review queue, page images and retry.

The RBAC coverage test in ``test_phase_5_rbac_coverage.py`` already asserts
black-box that every one of these paths refuses an unauthenticated request;
it enumerates the OpenAPI schema, so the endpoints added here were in its
scope the moment they were registered. What is tested here is the behaviour
behind the guard.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.auth import AuthenticatedUser
from app.services.documents import repository
from app.services.documents.storage import DocumentStore
from tests import _documents
from tests._phase5 import authenticate_as, build_world

pytestmark = pytest.mark.asyncio


async def _signed_in(client, session, monkeypatch, tmp_path, role="lab_tech"):
    """A temp document store, real user rows, and a signed-in client.

    ⚠️ **The users have to be real.** `documents.uploaded_by` is a foreign key
    to `users.id`, so the stub admin `authenticate_as` uses by default — an id
    that exists only in the test process — makes every upload fail on the FK.
    Phases 1–5 never noticed because none of their endpoints write the acting
    user's id into a new row.
    """
    store = DocumentStore(tmp_path)
    for module in (
        "app.services.documents.intake",
        "app.services.documents.pipeline",
        "app.routers.documents",
    ):
        monkeypatch.setattr(f"{module}.get_store", lambda *a, **k: store)

    ids = await build_world(session)
    await session.commit()

    key = {"lab_tech": "labtech", "auditor": "auditor", "admin": "admin"}[role]
    user = AuthenticatedUser(
        id=uuid.UUID(ids[key]),
        employee_code=f"{role.upper()}{ids['tag']}",
        full_name=f"{role} user",
        role=role,
        department_id=uuid.UUID(ids["dept"]),
        must_change_password=False,
    )
    authenticate_as(client._transport.app, user)  # type: ignore[attr-defined]
    return store, ids


async def test_upload_accepts_a_pdf_and_queues_extraction(
    client, session, monkeypatch, tmp_path
):
    """6.1's endpoint, at the URL the plan names."""
    await _signed_in(client, session, monkeypatch, tmp_path)

    response = await client.post(
        "/api/reports/upload",
        files={"file": ("report.pdf", _documents.native_pdf(), "application/pdf")},
    )

    # 202, not 201: nothing has been extracted yet. 201 would imply a
    # finished resource.
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["duplicate"] is False
    assert len(body["sha256"]) == 64
    assert body["mime_type"] == "application/pdf"
    # No scanner in the test environment -- and it says `skipped`, never
    # `clean`.
    assert body["virus_scan"] == "skipped"


async def test_upload_refuses_a_type_it_cannot_read(
    client, session, monkeypatch, tmp_path
):
    await _signed_in(client, session, monkeypatch, tmp_path)

    response = await client.post(
        "/api/reports/upload",
        files={
            "file": (
                "report.pdf",
                b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 512,
                "application/pdf",
            )
        },
    )

    # 422, not 400: the request was well-formed, its content was not
    # acceptable.
    assert response.status_code == 422
    # The message lands in `title`, not `detail`: `app/errors.py` maps a
    # string HTTPException detail onto the problem+json title, and every
    # endpoint since Phase 1 does the same. Consistency with that beats
    # being marginally closer to RFC 7807.
    assert "Upload a PDF" in response.json()["title"]


async def test_the_same_report_uploaded_twice_returns_the_same_document(
    client, session, monkeypatch, tmp_path
):
    await _signed_in(client, session, monkeypatch, tmp_path)
    data = _documents.native_pdf()

    first = await client.post(
        "/api/reports/upload", files={"file": ("a.pdf", data, "application/pdf")}
    )
    second = await client.post(
        "/api/reports/upload", files={"file": ("b.pdf", data, "application/pdf")}
    )

    assert first.json()["document_id"] == second.json()["document_id"]
    assert second.json()["duplicate"] is True
    assert "already in the system" in second.json()["message"]


async def test_the_review_queue_lists_documents_needing_a_human(
    client, session, monkeypatch, tmp_path
):
    await _signed_in(client, session, monkeypatch, tmp_path)

    upload = await client.post(
        "/api/reports/upload",
        files={"file": ("locked.pdf", _documents.encrypted_pdf(), "application/pdf")},
    )
    document_id = upload.json()["document_id"]

    import uuid as _uuid

    from app.services.documents import pipeline

    await pipeline.ingest_document(session, _uuid.UUID(document_id))

    response = await client.get("/api/documents/review-queue")

    assert response.status_code == 200
    ids = {row["id"] for row in response.json()["documents"]}
    assert document_id in ids


async def test_page_images_are_served_for_the_fallback_view(
    client, session, monkeypatch, tmp_path
):
    """★ 6.5's fallback needs the page image beside the entry form.

    Without this endpoint the review queue is a list of failures with nothing
    to read from.
    """
    import uuid as _uuid

    from app.services.documents import ocr, pipeline

    await _signed_in(client, session, monkeypatch, tmp_path)

    # Extraction runs in a child process, so patching the engine here would
    # not reach it. Swap the pool for a thread pool: this test is about the
    # endpoint serving a page image, not about process isolation — that is
    # covered by `test_a_child_that_is_killed_does_not_take_the_worker_with_it`.
    #
    # It also keeps the test fast. Real OCR on an A4 page takes ~40 seconds.
    import concurrent.futures

    monkeypatch.setattr(
        "app.services.documents.pipeline._get_pool",
        lambda: concurrent.futures.ThreadPoolExecutor(max_workers=1),
    )
    ocr.reset_engine()
    monkeypatch.setattr(
        ocr,
        "_get_engine",
        lambda _lang: (_ for _ in ()).throw(ocr.OcrUnavailableError("absent")),
    )

    upload = await client.post(
        "/api/reports/upload",
        files={"file": ("scan.pdf", _documents.scanned_pdf(), "application/pdf")},
    )
    document_id = upload.json()["document_id"]
    await pipeline.ingest_document(session, _uuid.UUID(document_id))

    detail = await client.get(f"/api/documents/{document_id}")
    assert detail.status_code == 200
    page = detail.json()["pages"][0]
    assert page["image_url"], "a scanned page must expose an image URL"

    image = await client.get(page["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")


async def test_retry_requeues_a_failed_document(client, session, monkeypatch, tmp_path):
    """6.5: *"DLQ + admin retry button."*"""
    import uuid as _uuid

    from app.services.documents import pipeline

    await _signed_in(client, session, monkeypatch, tmp_path)

    upload = await client.post(
        "/api/reports/upload",
        files={"file": ("locked.pdf", _documents.encrypted_pdf(), "application/pdf")},
    )
    document_id = upload.json()["document_id"]
    await pipeline.ingest_document(session, _uuid.UUID(document_id))

    response = await client.post(f"/api/documents/{document_id}/retry")

    assert response.status_code == 202, response.text
    document = await repository.get(session, _uuid.UUID(document_id))
    assert document is not None
    assert document.status == repository.STATUS_RECEIVED
    # The previous attempt's message must not linger on a document that is
    # queued to be tried again.
    assert document.error_text is None


async def test_retry_is_refused_while_a_document_is_in_flight(
    client, session, monkeypatch, tmp_path
):
    """Two workers writing pages for one document is the race this prevents."""
    import uuid as _uuid

    await _signed_in(client, session, monkeypatch, tmp_path)

    upload = await client.post(
        "/api/reports/upload",
        files={"file": ("r.pdf", _documents.native_pdf(), "application/pdf")},
    )
    document_id = upload.json()["document_id"]
    await repository.set_status(
        session, _uuid.UUID(document_id), repository.STATUS_EXTRACTING
    )
    await session.commit()

    response = await client.post(f"/api/documents/{document_id}/retry")

    assert response.status_code == 409
    assert "being processed" in response.json()["title"]


async def test_an_unknown_document_is_404_not_500(client, session):
    authenticate_as(client._transport.app)  # type: ignore[attr-defined]

    response = await client.get("/api/documents/00000000-0000-7000-8000-000000000000")

    assert response.status_code == 404


async def test_an_auditor_may_read_the_queue_but_not_retry(
    client, session, monkeypatch, tmp_path
):
    """An auditor reads the record; they do not act on it.

    Retrying overwrites pages and spans, which is a write to the thing they
    are auditing.
    """
    await _signed_in(client, session, monkeypatch, tmp_path, role="auditor")

    assert (await client.get("/api/documents/review-queue")).status_code == 200

    retry = await client.post(
        "/api/documents/00000000-0000-7000-8000-000000000000/retry"
    )
    assert retry.status_code == 403


async def test_an_auditor_may_not_upload(client, session, monkeypatch, tmp_path):
    """Uploading would put the auditor into the record they audit."""
    await _signed_in(client, session, monkeypatch, tmp_path, role="auditor")

    response = await client.post(
        "/api/reports/upload",
        files={"file": ("r.pdf", _documents.native_pdf(), "application/pdf")},
    )

    assert response.status_code == 403

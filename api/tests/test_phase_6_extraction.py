"""Phase 6.2 and 6.3 — type detection and the native text path.

No database. These are properties of the extraction code itself, and keeping
them out of the DB-backed suite means they run in milliseconds and fail with a
message about PDFs rather than about Postgres.
"""

from __future__ import annotations

import pytest

from app.services.documents import detect, native
from app.services.documents.settings import DEFAULT_SCANNED_CHAR_DENSITY
from tests import _documents

THRESHOLD = DEFAULT_SCANNED_CHAR_DENSITY


# ── 6.2 type detection ────────────────────────────────────────────
def test_native_pdf_is_not_scanned(tmp_path):
    path = _documents.write(tmp_path, "native.pdf", _documents.native_pdf())

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    assert result.ok
    assert result.page_count == 1
    assert result.pages[0].is_scanned is False
    assert result.pages[0].char_count > 0


def test_scanned_pdf_is_detected_as_scanned(tmp_path):
    path = _documents.write(tmp_path, "scan.pdf", _documents.scanned_pdf())

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    assert result.ok
    assert result.pages[0].is_scanned is True
    # An image-only page has no extractable characters at all.
    assert result.pages[0].char_count == 0


def test_mixed_document_is_decided_per_page(tmp_path):
    """6.2: *"Mixed documents: decide **per page**, not per document."*

    A real discharge packet is a digital cover sheet plus a scanned annexe. A
    whole-document verdict loses one half or the other.
    """
    path = _documents.write(tmp_path, "mixed.pdf", _documents.mixed_pdf())

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    assert result.ok
    assert result.page_count == 2
    assert [p.is_scanned for p in result.pages] == [False, True]


def test_density_is_scale_free_not_a_character_count(tmp_path):
    """The threshold is chars per unit area, so page size does not change the
    verdict for the same content density."""
    path = _documents.write(tmp_path, "native.pdf", _documents.native_pdf())
    result = detect.inspect(path, char_density_threshold=THRESHOLD)
    page = result.pages[0]

    expected = page.char_count / ((page.width_pt * page.height_pt) / 1000.0)
    assert page.char_density == pytest.approx(expected)


def test_encrypted_pdf_routes_to_review_not_an_exception(tmp_path):
    """6.2: *"Encrypted PDF → try empty password → else `needs_review`"*."""
    path = _documents.write(tmp_path, "locked.pdf", _documents.encrypted_pdf())

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    assert not result.ok
    assert result.failure is detect.OpenFailure.ENCRYPTED
    assert "password" in result.detail.lower()


def test_an_owner_locked_pdf_is_read_rather_than_sent_for_review(tmp_path):
    """The *"try empty password"* half of the same clause.

    This is the common case in hospitals — a document stamped with an owner
    password to discourage editing, still perfectly readable. Sending it to a
    human as "encrypted" would be a needless interruption.

    ⚠️ Note what is **not** asserted: ``decrypted_with_empty_password``. When
    the *user* password is empty, PyMuPDF does not set ``needs_pass`` at all,
    so the empty-password branch never runs and the flag stays False. That is
    the library behaving correctly, and an earlier version of this test
    asserted the flag instead of the outcome — testing our bookkeeping rather
    than the clause the plan actually asks for. The outcome is what matters:
    the document opens and its text is readable.
    """
    path = _documents.write(tmp_path, "owner.pdf", _documents.owner_locked_pdf())

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    assert result.ok, result.detail
    assert result.failure is None
    assert result.pages[0].is_scanned is False
    assert result.pages[0].char_count > 0


def test_corrupt_pdf_is_repaired_by_pikepdf(tmp_path):
    """6.2: *"Corrupt PDF → repair attempt via `pikepdf`"*."""
    path = _documents.write(tmp_path, "broken.pdf", _documents.corrupt_pdf())

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    # Either PyMuPDF tolerated the damage itself or pikepdf repaired it. What
    # must NOT happen is an exception or a failure verdict.
    assert result.ok, result.detail
    assert result.page_count == 1


def test_garbage_that_cannot_be_repaired_fails_with_a_clear_error(tmp_path):
    """*"else fail with a clear error"* — and clear means actionable."""
    path = _documents.write(tmp_path, "junk.pdf", b"this is not a PDF at all" * 50)

    result = detect.inspect(path, char_density_threshold=THRESHOLD)

    assert not result.ok
    assert result.failure is detect.OpenFailure.CORRUPT
    assert result.detail


# ── 6.3 native text and spans ─────────────────────────────────────
def test_every_span_indexes_the_text_layer_exactly(tmp_path):
    """★ **The invariant Phase 8's span verifier depends on.**

        page.text_layer[span.char_start:span.char_end] == span.text

    If this drifts by even one character, every citation the product ever
    shows points slightly to the left of the value it claims to quote — and
    the highlight overlay highlights the wrong number on a page of numbers.
    """
    path = _documents.write(tmp_path, "native.pdf", _documents.native_pdf(pages=2))

    pages = native.extract_native_pages(path, [1, 2])

    assert set(pages) == {1, 2}
    for page in pages.values():
        assert page.spans, "a page with text must produce spans"
        for span in page.spans:
            assert page.text[span.char_start : span.char_end] == span.text


def test_spans_are_ordered_and_do_not_overlap(tmp_path):
    path = _documents.write(tmp_path, "native.pdf", _documents.native_pdf())

    page = native.extract_native_pages(path, [1])[1]

    previous_end = -1
    for span in page.spans:
        assert span.char_start >= previous_end
        assert span.char_end > span.char_start
        previous_end = span.char_end


def test_every_span_carries_a_usable_rectangle(tmp_path):
    """A span with no rectangle cannot be highlighted, and a span that cannot
    be highlighted cannot be verified."""
    path = _documents.write(tmp_path, "native.pdf", _documents.native_pdf())

    page = native.extract_native_pages(path, [1])[1]

    for span in page.spans:
        assert set(span.bbox) == {"x0", "y0", "x1", "y1"}
        assert span.bbox["x1"] > span.bbox["x0"]
        assert span.bbox["y1"] > span.bbox["y0"]


def test_two_column_pages_are_read_a_column_at_a_time(tmp_path):
    """6.3: *"Preserve reading order; handle two-column layouts."*

    The failure this guards is interleaving: LEFT-00, RIGHT-00, LEFT-01 … On
    a lab report that pairs each analyte with the wrong column's number.
    """
    path = _documents.write(tmp_path, "cols.pdf", _documents.two_column_pdf())

    page = native.extract_native_pages(path, [1])[1]

    assert page.multi_column is True
    last_left = page.text.rfind("LEFT-11")
    first_right = page.text.find("RIGHT-00")
    assert last_left != -1, "LEFT-11 is missing from the extracted text"
    assert first_right != -1, "RIGHT-00 is missing from the extracted text"
    assert (
        last_left < first_right
    ), "the whole left column must be read before the right one begins"


# ── 6.4 rendering, bounded ────────────────────────────────────────
def test_an_enormous_page_is_rendered_within_the_size_cap(tmp_path):
    """★ The bitmap that took the worker down.

    Rendering at a fixed DPI ignores how big the page is. An oversized page
    produced a 15.5-megapixel bitmap, denoising it ran for minutes, the worker
    hit its memory limit and died, and pgmq redelivered the document **16
    times** — taking the SLA-timer and notification consumers with it on every
    restart.

    4000px is PaddleOCR's own `max_side_limit`, so every pixel above it was
    resized away by the engine anyway: pure cost, no accuracy.
    """
    import pymupdf

    from app.services.documents import render

    # 2000 x 2800 pt is roughly A2. At 200 DPI that is ~5555 x 7777 px.
    doc = pymupdf.open()
    page = doc.new_page(width=2000, height=2800)
    page.insert_text((100, 200), "Creatinine 2.8 mg/dL", fontsize=40)

    rendered = render.render_page(page, 1, dpi=200)
    doc.close()

    assert max(rendered.width_px, rendered.height_px) <= render.MAX_RENDER_SIDE_PX
    # And it really would have been over the limit without the cap.
    assert render.MAX_RENDER_SIDE_PX < 2800 * (200 / 72.0)
    assert rendered.effective_dpi < 200


def test_a_downscaled_page_still_maps_its_pixels_back_to_points(tmp_path):
    """The cap must not move the spans.

    ``points_per_pixel`` has to come from the zoom actually used. Deriving it
    from the *requested* DPI would put every span on a downscaled page in the
    wrong place — and a highlight in the wrong place is a citation pointing at
    the wrong number.
    """
    import pymupdf

    from app.services.documents import render

    doc = pymupdf.open()
    page = doc.new_page(width=2000, height=2800)
    rendered = render.render_page(page, 1, dpi=200)
    doc.close()

    # The full page width in pixels, converted back, must be the page width in
    # points -- whatever scaling happened in between.
    assert rendered.width_px * rendered.points_per_pixel == pytest.approx(
        2000, rel=0.01
    )
    assert rendered.height_px * rendered.points_per_pixel == pytest.approx(
        2800, rel=0.01
    )


def test_a_wide_page_is_bounded_by_total_pixels_not_just_its_longest_side():
    """★ The cap that actually bounds memory.

    A 2827 x 4000 render is inside the 4000px side limit and is still 11.3
    megapixels — and PaddleOCR on exactly that was **SIGKILLed by the OOM
    killer**. Recognition memory tracks total area, not the longest edge, so
    the side cap alone was not enough.
    """
    import pymupdf

    from app.services.documents import render

    # Deliberately near-square and large: well inside the side cap once
    # scaled, but far over the area cap.
    doc = pymupdf.open()
    page = doc.new_page(width=1200, height=1700)
    rendered = render.render_page(page, 1, dpi=200)
    doc.close()

    pixels = rendered.width_px * rendered.height_px
    assert pixels <= render.MAX_RENDER_PIXELS, f"{pixels} pixels is over the cap"
    # ...and it really would have been over without the cap: 1200x1700pt at
    # 200 DPI is ~3333x4722 = 15.7 MP.
    assert render.MAX_RENDER_PIXELS < (1200 * 1700 * (200 / 72.0) ** 2)


def test_a_normal_page_is_not_downscaled(tmp_path):
    """The cap is a backstop, not a quality reduction for ordinary pages."""
    import pymupdf

    from app.services.documents import render

    doc = pymupdf.open()  # A4 by default
    page = doc.new_page()
    rendered = render.render_page(page, 1, dpi=200)
    doc.close()

    assert rendered.effective_dpi == pytest.approx(200.0)
    assert rendered.points_per_pixel == pytest.approx(72.0 / 200.0)


def test_denoising_takes_the_cheap_path_on_a_huge_image():
    """Non-local-means is O(pixels x window) and must never be the path that
    can hang. Above the limit it degrades to a median blur, which finishes."""
    import numpy as np

    from app.services.documents import preprocess

    big = np.full((3000, 3000), 200, dtype=np.uint8)
    assert big.size > preprocess.MAX_DENOISE_PIXELS

    calls: list[str] = []
    import cv2

    real_nlm = cv2.fastNlMeansDenoising

    def spy(*args, **kwargs):  # pragma: no cover - should not be reached
        calls.append("nlm")
        return real_nlm(*args, **kwargs)

    cv2.fastNlMeansDenoising = spy
    try:
        result = preprocess.preprocess(big, dpi=200)
    finally:
        cv2.fastNlMeansDenoising = real_nlm

    assert calls == [], "the slow denoiser must not run on an oversized image"
    assert result.image.shape == big.shape


def test_a_scanned_page_yields_no_native_text(tmp_path):
    """Belt and braces for the routing decision: if this ever returned text,
    detect.py's verdict and native.py's output would disagree."""
    path = _documents.write(tmp_path, "scan.pdf", _documents.scanned_pdf())

    page = native.extract_native_pages(path, [1])[1]

    assert page.text.strip() == ""
    assert page.spans == []

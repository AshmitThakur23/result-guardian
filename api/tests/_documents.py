"""Synthetic PDFs for Phase 6's unit tests.

⚠️ **These are test fixtures and nothing else.**
[`phase-06-document-ingestion.md`](../../docs/build/phase-06-document-ingestion.md)
is explicit:

    do not fake it with synthetic PDFs, they will not surface the failure
    modes real labs produce

That prohibition is about **Exit Gate 6**, which requires 200 *real* PDFs and
stays 🔴 OPEN. Nothing generated here is evidence for it, and none of these
files may ever be counted toward the corpus.

What they legitimately test is the plumbing: that a two-column page comes out
in reading order, that an encrypted file routes to ``needs_review`` rather
than crashing, that span offsets index the text layer exactly. Those are
properties of our code, and our code is what a unit test is for. Whether a
particular lab's fax header defeats the scanned-page heuristic is a property
of that lab's paper, and only real documents can answer it.
"""

from __future__ import annotations

import io
from pathlib import Path


def native_pdf(*, pages: int = 1, text: str = "Haemoglobin 9.2 g/dL  (Low)") -> bytes:
    """A normal digital PDF with a real text layer."""
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"{text} — page {index + 1}", fontsize=11)
        page.insert_text((72, 130), "Reference range 12.0 - 15.0 g/dL", fontsize=11)
    out = doc.tobytes()
    doc.close()
    return bytes(out)


def two_column_pdf() -> bytes:
    """Two columns with a wide gutter.

    The left column must be read in full before the right one. Interleaving
    them line by line is the classic failure, and on a lab report it pairs
    each analyte with the wrong column's number.
    """
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()  # 595 x 842 pt, A4
    for i in range(12):
        y = 100 + i * 20
        page.insert_text((60, y), f"LEFT-{i:02d}", fontsize=11)
        # x=340 leaves a gutter from ~120 to 340 -- far wider than
        # native.MIN_GUTTER_PT, and it runs the full height of the text.
        page.insert_text((340, y), f"RIGHT-{i:02d}", fontsize=11)
    out = doc.tobytes()
    doc.close()
    return bytes(out)


def scanned_pdf(*, pages: int = 1) -> bytes:
    """A PDF whose pages are images — no text layer at all.

    Built by rendering a text page to a pixmap and inserting the image, which
    is exactly what a scanner or a fax gateway produces.

    ⚠️ The new page keeps the **source page's own size in points**. An earlier
    version sized it from the pixmap's pixel count, which produced a page
    twice A4 — and a page twice A4 renders to four times the pixels. That is
    not what a scanner produces, and it made every timing measurement taken
    from this fixture meaningless. A scanner photographs an A4 sheet and
    hands back an A4 page carrying a higher-resolution image.
    """
    import pymupdf

    source = pymupdf.open(stream=native_pdf(pages=pages), filetype="pdf")
    doc = pymupdf.open()
    for index in range(source.page_count):
        source_page = source.load_page(index)
        pixmap = source_page.get_pixmap(dpi=150)
        rect = source_page.rect
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_image(page.rect, pixmap=pixmap)
    out = doc.tobytes()
    source.close()
    doc.close()
    return bytes(out)


def mixed_pdf() -> bytes:
    """Page 1 native, page 2 scanned. 6.2: *decide per page, not per
    document.*"""
    import pymupdf

    doc = pymupdf.open(stream=native_pdf(pages=1), filetype="pdf")
    scanned = pymupdf.open(stream=scanned_pdf(pages=1), filetype="pdf")
    doc.insert_pdf(scanned)
    out = doc.tobytes()
    scanned.close()
    doc.close()
    return bytes(out)


def encrypted_pdf(*, password: str = "secret") -> bytes:
    """Password-protected with a password we do not have."""
    import pymupdf

    doc = pymupdf.open(stream=native_pdf(), filetype="pdf")
    buffer = io.BytesIO()
    doc.save(
        buffer,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw=password,
        user_pw=password,
    )
    doc.close()
    return buffer.getvalue()


def owner_locked_pdf() -> bytes:
    """Owner password only — the empty *user* password opens it.

    6.2: *"Encrypted PDF → try empty password"*. This is the case that clause
    exists for, and it is common: hospital systems stamp an owner password on
    a document to discourage editing, leaving it readable.
    """
    import pymupdf

    doc = pymupdf.open(stream=native_pdf(), filetype="pdf")
    buffer = io.BytesIO()
    doc.save(
        buffer,
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-only",
        user_pw="",
        permissions=pymupdf.PDF_PERM_ACCESSIBILITY,
    )
    doc.close()
    return buffer.getvalue()


def corrupt_pdf() -> bytes:
    """A PDF with its cross-reference table destroyed.

    Truncating the trailer is the damage that actually happens in transit --
    an interrupted fax, a truncated SMB copy. QPDF (under pikepdf) recovers
    from it by rebuilding the xref from the objects it can still find, which
    is what 6.2's repair step is for.
    """
    data = native_pdf()
    marker = data.rfind(b"startxref")
    if marker == -1:  # pragma: no cover - PyMuPDF always writes one
        return data[: len(data) // 2]
    return data[:marker] + b"startxref\n999999999\n%%EOF\n"


def not_a_pdf() -> bytes:
    """PNG bytes. Used with a ``.pdf`` filename to prove the sniffer ignores
    the extension."""
    import pymupdf

    doc = pymupdf.open(stream=native_pdf(), filetype="pdf")
    png = doc.load_page(0).get_pixmap(dpi=72).tobytes("png")
    doc.close()
    return bytes(png)


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path

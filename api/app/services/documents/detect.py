"""Opening a PDF, and deciding what kind of page each page is. Phase 6.2.

    - PyMuPDF: extract text per page; if `extractable chars / page area <
      threshold` → treat as scanned
    - Mixed documents: decide **per page**, not per document
    - Encrypted PDF → try empty password → else `needs_review`
    - Corrupt PDF → repair attempt via `pikepdf` → else fail with a clear error

The per-page rule is the one that matters clinically. A real discharge packet
is routinely a digital cover sheet stapled to a scanned annexe, and a
whole-document verdict loses one half or the other: call it native and the
scanned pages come back empty; call it scanned and the native pages get OCRed
at a fraction of the accuracy the text layer already had.

**Density, not a raw count.** A count would call a sparse A3 chart "scanned"
and a dense compliment slip "native". Characters per unit of page area is
scale-free, which is what lets one threshold work across page sizes.

On encryption and corruption the module is deliberately unlike the rest of the
pipeline: it does not raise. Both produce a *typed outcome*, because 6.2 wants
an encrypted file routed to a human and a corrupt one reported with a clear
error — not a stack trace in a worker log.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# Area is measured in thousands of pt² so the threshold is a readable number
# rather than 0.0005.
AREA_UNIT_PT2 = 1000.0


class OpenFailure(StrEnum):
    """Why a document could not be opened. Each maps to a different outcome."""

    # Password-protected with a password we do not have. 6.2 says
    # `needs_review` -- a human may know the password, and the file is
    # certainly not corrupt.
    ENCRYPTED = "encrypted"
    # Structurally broken past pikepdf's ability to repair it.
    CORRUPT = "corrupt"
    # Opened, but has no pages at all.
    EMPTY = "empty"


class DocumentOpenError(Exception):
    """Raised only by :func:`open_document` callers that want an exception.

    :func:`inspect` itself returns an outcome instead.
    """

    def __init__(self, failure: OpenFailure, detail: str) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail


@dataclass(frozen=True)
class PageKind:
    page_no: int
    width_pt: float
    height_pt: float
    is_scanned: bool
    char_count: int
    char_density: float


@dataclass(frozen=True)
class Inspection:
    """What we learned by opening the file. ``failure`` set means nothing else
    is meaningful except ``detail``."""

    page_count: int
    pages: tuple[PageKind, ...]
    # True when pikepdf had to rewrite the file to make it readable. Recorded
    # because a repaired document is one to look at twice, not one to trust
    # silently.
    repaired: bool
    # True when the file was encrypted and the empty password worked. The
    # content is fine; the provenance is worth knowing.
    decrypted_with_empty_password: bool
    failure: OpenFailure | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.failure is None

    @property
    def has_scanned_pages(self) -> bool:
        return any(p.is_scanned for p in self.pages)


def _char_density(chars: int, width_pt: float, height_pt: float) -> float:
    area = width_pt * height_pt
    if area <= 0:
        # A page with no area cannot have a density. Treat it as scanned:
        # scanned is the conservative verdict, because it sends the page
        # through OCR rather than recording it as legitimately blank.
        return 0.0
    return chars / (area / AREA_UNIT_PT2)


def inspect(path: Path, *, char_density_threshold: float) -> Inspection:
    """Open the file, repair or decrypt if needed, and classify every page.

    Never raises for a bad document — a bad document is an expected input
    here, and the caller needs to route it, not catch it.
    """
    # `pymupdf`, not the legacy `fitz` alias — PyMuPDF deprecated it and warns
    # on import. Lazy, because the API process only needs it on the retry
    # path; the worker is what does extraction.
    import pymupdf

    repaired = False
    decrypted = False
    data = path.read_bytes()

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        # Not openable at all. 6.2: "repair attempt via pikepdf".
        repaired_bytes = _try_repair(data)
        if repaired_bytes is None:
            return Inspection(
                page_count=0,
                pages=(),
                repaired=False,
                decrypted_with_empty_password=False,
                failure=OpenFailure.CORRUPT,
                detail=f"unreadable PDF, and pikepdf could not repair it: {exc}",
            )
        try:
            doc = pymupdf.open(stream=repaired_bytes, filetype="pdf")
        except Exception as exc2:
            return Inspection(
                page_count=0,
                pages=(),
                repaired=False,
                decrypted_with_empty_password=False,
                failure=OpenFailure.CORRUPT,
                detail=f"repaired PDF is still unreadable: {exc2}",
            )
        repaired = True

    with doc:
        if doc.needs_pass:
            # 6.2: "try empty password → else needs_review". Many hospital
            # systems stamp an owner password on a document that is otherwise
            # readable; an empty user password opens exactly those.
            if doc.authenticate(""):
                decrypted = True
            else:
                return Inspection(
                    page_count=0,
                    pages=(),
                    repaired=repaired,
                    decrypted_with_empty_password=False,
                    failure=OpenFailure.ENCRYPTED,
                    detail=(
                        "PDF is password-protected and the empty password did "
                        "not open it"
                    ),
                )

        page_count = doc.page_count
        if page_count == 0:
            return Inspection(
                page_count=0,
                pages=(),
                repaired=repaired,
                decrypted_with_empty_password=decrypted,
                failure=OpenFailure.EMPTY,
                detail="PDF contains no pages",
            )

        pages: list[PageKind] = []
        for index in range(page_count):
            page = doc.load_page(index)
            rect = page.rect
            # `get_text("text")` is the cheap probe: we only need to know how
            # much extractable text exists, not where it is. The expensive
            # word-and-bbox pass happens in native.py, and only for the pages
            # this step calls native.
            raw = page.get_text("text") or ""
            chars = len(raw.strip())
            density = _char_density(chars, rect.width, rect.height)
            pages.append(
                PageKind(
                    page_no=index + 1,
                    width_pt=float(rect.width),
                    height_pt=float(rect.height),
                    is_scanned=density < char_density_threshold,
                    char_count=chars,
                    char_density=density,
                )
            )

    log.info(
        "document_inspected",
        page_count=page_count,
        scanned_pages=sum(1 for p in pages if p.is_scanned),
        repaired=repaired,
        decrypted=decrypted,
    )
    return Inspection(
        page_count=page_count,
        pages=tuple(pages),
        repaired=repaired,
        decrypted_with_empty_password=decrypted,
    )


def _try_repair(data: bytes) -> bytes | None:
    """pikepdf rewrite. Returns the repaired bytes, or None.

    pikepdf is built on QPDF, whose parser recovers from the damage that
    actually happens to PDFs in transit — a truncated xref table, a broken
    stream length, a file concatenated twice. Saving it out rewrites a valid
    structure around the objects it could still find.

    **The repaired bytes are not written back over the original.** The stored
    file is content-addressed; overwriting it would make it no longer hash to
    its own name. The repair is used for this read only.
    """
    import io

    try:
        import pikepdf
    except ImportError:  # pragma: no cover - dependency is pinned
        log.warning("pikepdf_unavailable")
        return None

    try:
        with pikepdf.open(io.BytesIO(data), allow_overwriting_input=False) as pdf:
            out = io.BytesIO()
            pdf.save(out)
            log.info("pdf_repaired_by_pikepdf")
            return out.getvalue()
    except Exception as exc:
        log.info("pdf_repair_failed", error=str(exc))
        return None

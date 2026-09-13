"""A very small PDF writer. Phase 5.6.

    Export to CSV; monthly PDF for NABH review (**AAC.12, AAC.6.g**)

**Why not ReportLab or WeasyPrint.** The stack is locked, and the one thing
this project needs a PDF for is a monthly table of numbers with a header. A
PDF library brings 5–40 MB of dependency, a C toolchain in some cases, and a
new supply-chain surface onto a machine that holds patient data — for
something the format's own specification makes achievable in a couple of
hundred lines.

So: a single page size, the base-14 Helvetica fonts every reader has built
in, and no images. What that buys is a file with **no third-party code in the
path between a clinical metric and an accreditation document**.

What it costs is worth writing down: no Unicode beyond WinAnsi (a Hindi
department name would be transliterated by :func:`_winansi`, not rendered),
no embedded fonts, no wrapping cleverness. If the hospital ever wants a
designed report, that is the moment to reconsider — not before.
"""

from __future__ import annotations

import datetime as dt
import zlib
from dataclasses import dataclass, field

# A4 in points, the Indian standard page size.
PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN = 48.0
LINE_HEIGHT = 14.0
BODY_SIZE = 9.5
HEADING_SIZE = 15.0
SUBHEADING_SIZE = 11.0


def _winansi(value: str) -> str:
    """Encode to WinAnsi, replacing what will not fit.

    Silently dropping characters would turn a patient's name into a different
    name. ``?`` is visibly wrong, which is the correct failure for a document
    someone signs.
    """
    return value.encode("cp1252", errors="replace").decode("cp1252")


def _escape(value: str) -> str:
    return _winansi(value).replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


@dataclass
class _Page:
    ops: list[str] = field(default_factory=list)


class PdfBuilder:
    """Builds a multi-page, text-only PDF."""

    def __init__(self, *, title: str) -> None:
        self.title = title
        self._pages: list[_Page] = []
        self._page: _Page | None = None
        self._y = 0.0
        self._new_page()

    # ── layout ────────────────────────────────────────────────────
    def _new_page(self) -> None:
        self._page = _Page()
        self._pages.append(self._page)
        self._y = PAGE_HEIGHT - MARGIN

    def _ensure(self, needed: float) -> None:
        if self._y - needed < MARGIN:
            self._new_page()

    def _text(self, x: float, size: float, value: str, *, bold: bool = False) -> None:
        assert self._page is not None
        font = "F2" if bold else "F1"
        self._page.ops.append(
            f"BT /{font} {size} Tf 1 0 0 1 {x:.2f} {self._y:.2f} Tm "
            f"({_escape(value)}) Tj ET"
        )

    def heading(self, value: str) -> None:
        self._ensure(HEADING_SIZE + 8)
        self._text(MARGIN, HEADING_SIZE, value, bold=True)
        self._y -= HEADING_SIZE + 6

    def subheading(self, value: str) -> None:
        self._ensure(SUBHEADING_SIZE + 10)
        self._y -= 6
        self._text(MARGIN, SUBHEADING_SIZE, value, bold=True)
        self._y -= SUBHEADING_SIZE + 2
        self.rule()

    def line(self, value: str = "", *, indent: float = 0.0) -> None:
        self._ensure(LINE_HEIGHT)
        if value:
            self._text(MARGIN + indent, BODY_SIZE, value)
        self._y -= LINE_HEIGHT

    def row(self, left: str, right: str, *, indent: float = 0.0) -> None:
        """A label/value row. The value is right-of-centre, not right-aligned:
        proportional metrics with no font-metric table cannot be measured, and
        a fixed column is honest about that."""
        self._ensure(LINE_HEIGHT)
        self._text(MARGIN + indent, BODY_SIZE, left)
        self._text(MARGIN + 330, BODY_SIZE, right)
        self._y -= LINE_HEIGHT

    def rule(self) -> None:
        assert self._page is not None
        self._ensure(6)
        self._page.ops.append(
            f"0.7 w 0.6 0.6 0.6 RG {MARGIN:.2f} {self._y:.2f} m "
            f"{PAGE_WIDTH - MARGIN:.2f} {self._y:.2f} l S"
        )
        self._y -= 8

    def spacer(self, height: float = 8.0) -> None:
        self._ensure(height)
        self._y -= height

    # ── output ────────────────────────────────────────────────────
    def build(self) -> bytes:
        """Assemble the file.

        Objects are written in order with a real cross-reference table — a
        reader is entitled to seek by offset, and a PDF with a wrong xref
        opens in some viewers and not in others, which is the worst kind of
        broken.
        """
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        font_regular = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        )
        font_bold = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        )

        # Reserve the Pages object number so each page can name its parent.
        pages_number = len(objects) + 1
        objects.append(b"")  # placeholder, filled in below

        page_numbers: list[int] = []
        for index, page in enumerate(self._pages, start=1):
            footer = (
                f"BT /F1 8 Tf 1 0 0 1 {MARGIN:.2f} {MARGIN - 18:.2f} Tm "
                f"({_escape(f'Page {index} of {len(self._pages)}')}) Tj ET"
            )
            stream = ("\n".join([*page.ops, footer])).encode("latin-1", "replace")
            compressed = zlib.compress(stream)
            content_number = add(
                b"<< /Length "
                + str(len(compressed)).encode()
                + b" /Filter /FlateDecode >>\nstream\n"
                + compressed
                + b"\nendstream"
            )
            page_numbers.append(
                add(
                    f"<< /Type /Page /Parent {pages_number} 0 R "
                    f"/MediaBox [0 0 {PAGE_WIDTH:.2f} {PAGE_HEIGHT:.2f}] "
                    f"/Resources << /Font << /F1 {font_regular} 0 R "
                    f"/F2 {font_bold} 0 R >> >> "
                    f"/Contents {content_number} 0 R >>".encode()
                )
            )

        kids = " ".join(f"{n} 0 R" for n in page_numbers)
        objects[pages_number - 1] = (
            f"<< /Type /Pages /Count {len(page_numbers)} /Kids [{kids}] >>".encode()
        )

        stamp = dt.datetime.now(dt.UTC).strftime("D:%Y%m%d%H%M%SZ")
        info_number = add(
            f"<< /Title ({_escape(self.title)}) /Producer (Result Guardian) "
            f"/CreationDate ({stamp}) >>".encode()
        )
        catalog_number = add(f"<< /Type /Catalog /Pages {pages_number} 0 R >>".encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_number} 0 R "
            f"/Info {info_number} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
        )
        return bytes(out)

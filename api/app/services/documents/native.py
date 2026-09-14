"""Native text extraction, with coordinates. Phase 6.3.

    - PyMuPDF `get_text("dict")` → words with bboxes → build char-offset ↔
      bbox map
    - Docling for pages containing detected table structures (**lab reports
      are tables**)
    - Preserve reading order; handle two-column layouts

**The invariant this module exists to uphold:**

    page.text_layer[span.char_start:span.char_end] == span.text

Exactly, for every span, with no normalisation in between. Phase 8's span
verifier is plain code that checks a quotation appears in the source — it can
only do that if the offsets address the same string the rest of the system
reads. So the text layer is *built from* the spans rather than extracted
separately and matched up afterwards: there is no second extraction that could
disagree.

The alternative — extract text, extract words, then fuzzy-align them — is how
citations end up pointing three characters to the left. It is also how you get
an overlay that highlights the wrong number on a page of numbers.

**Everything here works on words, not on PyMuPDF's blocks.** That is not a
style choice; it is the fix for a defect this module shipped with. See
:func:`_collect_words`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Two words are on the same line if their vertical centres are within this
# fraction of the first word's height. Generous, because superscript unit
# markers and subscripts sit off the baseline and still belong to the line.
LINE_TOLERANCE = 0.6

# A gutter must be at least this wide (in points) to be believed as a column
# separator. Below it, the "gap" is just inter-word space in a wide table.
MIN_GUTTER_PT = 24.0

# ...and must run down at least this much of the page's text height. A gap
# that exists for two lines is a table cell boundary, not a column.
MIN_GUTTER_COVERAGE = 0.55


@dataclass(frozen=True)
class Span:
    """One word, and the rectangle it occupied."""

    page_no: int
    char_start: int
    char_end: int
    bbox: dict[str, float]
    text: str


@dataclass(frozen=True)
class _Word:
    """A word with its box, before any ordering has been decided."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def centre_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def centre_y(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class ExtractedPage:
    page_no: int
    text: str
    spans: list[Span] = field(default_factory=list)
    # True when the page's reading order was resolved as two or more columns.
    # Recorded so a mis-ordered page can be recognised later without re-running
    # the heuristic.
    multi_column: bool = False
    # Tables found by PyMuPDF's structural detector, as row-major cell text.
    # Lab reports are tables, and a table flattened into prose loses which
    # value belongs to which analyte.
    tables: list[list[list[str]]] = field(default_factory=list)


def _bbox(x0: float, y0: float, x1: float, y1: float) -> dict[str, float]:
    # Rounded to 2dp: PDF coordinates are in points, and the third decimal is
    # below the resolution of anything a human will look at. It also keeps the
    # JSONB small and makes the values stable across PyMuPDF patch releases.
    return {
        "x0": round(float(x0), 2),
        "y0": round(float(y0), 2),
        "x1": round(float(x1), 2),
        "y1": round(float(y1), 2),
    }


def _collect_words(raw: dict[str, Any]) -> list[_Word]:
    """Flatten PyMuPDF's block/line/span tree into individual words.

    **Words, not blocks, are the unit — and that is the fix for a defect this
    module shipped with.** PyMuPDF groups text by baseline, so a two-column
    page whose columns share baselines comes back as *one block per row*,
    spanning the full page width. A full-width block leaves no gutter for any
    block-level heuristic to find, so the page was reported as one column and
    the text came out interleaved: ``LEFT-00 RIGHT-00 LEFT-01 …``. On a lab
    report that pairs each analyte with the wrong column's number.

    Individual words have narrow boxes with real white space between them, so
    the gutter is visible exactly where it exists. Caught by
    ``test_two_column_pages_are_read_a_column_at_a_time``, not by reading.
    """
    words: list[_Word] = []
    # Type 0 blocks are text; type 1 blocks are images and carry no words.
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for piece in line.get("spans", []):
                piece_text = piece.get("text", "")
                if not piece_text.strip():
                    continue
                # PyMuPDF's "span" is a run of same-styled text, which can be
                # several words. Split it so a citation can point at one word
                # -- keeping the run's box, interpolated across it by character
                # position. Exact vertically, approximate horizontally, which
                # is what a highlight needs.
                x0, y0, x1, y1 = (float(v) for v in piece["bbox"])
                run_width = x1 - x0
                run_len = max(len(piece_text), 1)
                cursor = 0
                for word in piece_text.split():
                    start = piece_text.find(word, cursor)
                    if start < 0:  # pragma: no cover - split and find agree
                        start = cursor
                    cursor = start + len(word)
                    words.append(
                        _Word(
                            x0=x0 + run_width * start / run_len,
                            y0=y0,
                            x1=x0 + run_width * cursor / run_len,
                            y1=y1,
                            text=word,
                        )
                    )
    return words


def _find_gutters(
    words: list[_Word], page_width: float, page_height: float
) -> list[float]:
    """Find vertical bands no word crosses — the columns' gutters.

    Sweep the page in 4pt slices and mark those a word overlaps. A run of
    empty slices wider than :data:`MIN_GUTTER_PT`, not touching either margin,
    with real content on both sides covering most of the page's text height,
    is a gutter.
    """
    if not words or page_width <= 0:
        return []

    step = 4.0
    slots = int(page_width // step) + 2
    occupied = [False] * slots
    for word in words:
        lo = max(int(word.x0 // step), 0)
        hi = min(int(word.x1 // step) + 1, slots)
        for i in range(lo, hi):
            occupied[i] = True

    # The vertical extent actually used by text, so a short page of two
    # columns is not disqualified by its own margins.
    top = min(w.y0 for w in words)
    bottom = max(w.y1 for w in words)
    used_height = bottom - top
    if used_height <= 0 or used_height < page_height * 0.2:
        # Too little text on the page to infer a layout from. One column.
        return []

    gutters: list[float] = []
    run_start: int | None = None
    for i, taken in enumerate([*occupied, True]):
        if not taken:
            if run_start is None:
                run_start = i
            continue
        if run_start is None:
            continue

        width = (i - run_start) * step
        centre = (run_start + i) / 2 * step
        # Ignore runs touching the page edges: those are margins, not gutters.
        touches_edge = run_start == 0 or i >= slots
        run_start = None
        if width < MIN_GUTTER_PT or touches_edge:
            continue

        left = [w for w in words if w.x1 <= centre]
        right = [w for w in words if w.x0 >= centre]
        if not left or not right:
            continue
        # Both sides must carry content down most of the page's text height --
        # otherwise this is the white space inside one wide table, not a
        # column boundary.
        coverage = min(
            max(w.y1 for w in left) - min(w.y0 for w in left),
            max(w.y1 for w in right) - min(w.y0 for w in right),
        )
        if coverage >= used_height * MIN_GUTTER_COVERAGE:
            gutters.append(centre)
    return gutters


def _group_into_lines(words: list[_Word]) -> list[list[_Word]]:
    """Group words sharing a baseline, left to right within each line."""
    if not words:
        return []

    lines: list[list[_Word]] = []
    for word in sorted(words, key=lambda w: (w.y0, w.x0)):
        placed = False
        if lines:
            current = lines[-1]
            reference = current[0]
            height = max(reference.y1 - reference.y0, 1e-6)
            if abs(word.centre_y - reference.centre_y) <= height * LINE_TOLERANCE:
                current.append(word)
                placed = True
        if not placed:
            lines.append([word])

    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _reading_order(
    words: list[_Word], width: float, height: float
) -> tuple[list[list[_Word]], bool]:
    """Order the page's words the way a person reads them.

    Single column: top to bottom, left to right within a line. Two columns:
    **the whole left column, then the whole right column** — which is the
    difference between a readable lab report and a sentence from each column
    interleaved.
    """
    gutters = _find_gutters(words, width, height)
    if not gutters:
        return _group_into_lines(words), False

    right_edge = max(width, max(w.x1 for w in words) + 1.0)
    bounds = [0.0, *sorted(gutters), right_edge]
    columns: list[list[_Word]] = [[] for _ in range(len(bounds) - 1)]
    for word in words:
        index = len(columns) - 1
        for c in range(len(bounds) - 1):
            if bounds[c] <= word.centre_x < bounds[c + 1]:
                index = c
                break
        columns[index].append(word)

    lines: list[list[_Word]] = []
    for column in columns:
        lines.extend(_group_into_lines(column))
    return lines, True


def extract_page(page: object, page_no: int) -> ExtractedPage:
    """Build one page's text layer and its spans together.

    Words are joined by single spaces within a line and separated by a newline
    between lines. **Each word's offsets are recorded as it is appended**, by
    the same loop that builds the string — so
    ``text[char_start:char_end] == span.text`` holds by construction, rather
    than by a later alignment pass that could drift.
    """
    raw = page.get_text("dict")  # type: ignore[attr-defined]
    rect = page.rect  # type: ignore[attr-defined]

    words = _collect_words(raw)
    lines, multi_column = _reading_order(words, float(rect.width), float(rect.height))

    parts: list[str] = []
    spans: list[Span] = []
    cursor = 0

    for line_index, line in enumerate(lines):
        if line_index:
            parts.append("\n")
            cursor += 1
        for word_index, word in enumerate(line):
            if word_index:
                parts.append(" ")
                cursor += 1
            start = cursor
            parts.append(word.text)
            cursor += len(word.text)
            spans.append(
                Span(
                    page_no=page_no,
                    char_start=start,
                    char_end=cursor,
                    bbox=_bbox(word.x0, word.y0, word.x1, word.y1),
                    text=word.text,
                )
            )

    extracted = ExtractedPage(
        page_no=page_no,
        text="".join(parts),
        spans=spans,
        multi_column=multi_column,
    )
    extracted.tables = _find_tables(page)
    return extracted


def _find_tables(page: object) -> list[list[list[str]]]:
    """Detect table structure. 6.3: **lab reports are tables.**

    A lab report flattened into prose loses the column a value sat in, which
    is the column that says whether 14.2 is the result or the reference range.
    Keeping the grid is what lets Phase 7 extract analytes by position rather
    than by guessing from word order.
    """
    finder = getattr(page, "find_tables", None)
    if finder is None:  # pragma: no cover - PyMuPDF is pinned >= 1.24
        return []
    try:
        found = finder()
    except Exception as exc:
        # A detector that throws on an unusual page must not take the page's
        # text with it. The text layer and its spans are already built.
        log.info("table_detection_failed", error=str(exc))
        return []

    tables: list[list[list[str]]] = []
    for table in getattr(found, "tables", []):
        try:
            rows = [[(cell or "").strip() for cell in row] for row in table.extract()]
        except Exception:  # pragma: no cover - defensive, same reason
            continue
        if rows:
            tables.append(rows)
    return tables


def extract_native_pages(
    path: Path, page_numbers: list[int]
) -> dict[int, ExtractedPage]:
    """Extract the given 1-based pages. Pages not listed are not touched."""
    import pymupdf

    wanted = set(page_numbers)
    out: dict[int, ExtractedPage] = {}
    with pymupdf.open(str(path)) as doc:
        if doc.needs_pass:
            doc.authenticate("")
        for index in range(doc.page_count):
            page_no = index + 1
            if page_no not in wanted:
                continue
            out[page_no] = extract_page(doc.load_page(index), page_no)
    return out

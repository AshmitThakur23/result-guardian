"""Tying an extracted value back to the rectangle it came from. Phase 7.3.

7.3's last bullet, and the one that is easiest to skip:

> **Every field carries its `document_span_id`.**

Without it a value is a number somebody's parser produced. With it a clinician
can be shown *the pixels on the page* the number was read from — which is the
difference between "the system says potassium 6.9" and "here is the line on the
lab's own report that says potassium 6.9". Phase 8's span verifier is built on
exactly the same idea one layer up, and neither works without offsets.

## Why matching is by offset and not by text

Phase 6 stores every span with ``char_start``/``char_end`` into the page's
``text_layer``, and asserts the invariant ``text_layer[start:end] == span.text``.
So the reliable way to find the span behind an extracted value is to locate the
value **in the same text layer** and find the span whose range contains it.

Searching spans by their text would be wrong twice over: ``6.9`` appears in a
dozen places on a report, and the *first* match is rarely the right one. An
offset is unambiguous; a string is not.

## What this returns when it is unsure

``None``. A wrong span points a clinician at the wrong line of a real report,
which is worse than pointing them at nothing — they would read it, see a
mismatch, and lose confidence in every citation the product ever shows. An
absent link is honest; a wrong one is corrosive.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def find_span_for_offset(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    page_no: int,
    char_offset: int,
) -> uuid.UUID | None:
    """The span whose range contains ``char_offset`` on this page.

    ``ORDER BY (char_end - char_start)`` picks the **tightest** enclosing span
    when several overlap — a word rather than the line, and a line rather than
    the paragraph. The narrowest true span is the most useful thing to highlight.
    """
    row = (
        await session.execute(
            text(
                "SELECT id FROM document_spans "
                " WHERE document_id = :d AND page_no = :p "
                "   AND deleted_at IS NULL "
                "   AND char_start <= :o AND char_end > :o "
                " ORDER BY (char_end - char_start) ASC "
                " LIMIT 1"
            ),
            {"d": str(document_id), "p": page_no, "o": char_offset},
        )
    ).first()
    return row.id if row else None


async def locate_value(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    test_name: str,
    value_raw: str,
) -> tuple[uuid.UUID | None, int | None]:
    """Find the span for ``value_raw``, anchored by the test name beside it.

    Returns ``(span_id, page_no)``, either of which may be ``None``.

    **Anchoring on the test name is what makes this safe.** ``6.9`` alone occurs
    all over a report — in a reference range, a previous result, a page number.
    Searching for the value *after* the position of its own analyte name pins it
    to the right row, and a value that does not appear after its name is
    reported as not found rather than matched to the nearest lookalike.
    """
    if not value_raw or not test_name:
        return (None, None)

    pages = (
        await session.execute(
            text(
                "SELECT page_no, text_layer FROM document_pages "
                " WHERE document_id = :d AND deleted_at IS NULL "
                "   AND text_layer IS NOT NULL "
                " ORDER BY page_no"
            ),
            {"d": str(document_id)},
        )
    ).fetchall()

    for page in pages:
        layer: str = page.text_layer
        name_at = layer.find(test_name)
        if name_at < 0:
            # Try case-insensitively before giving up on this page: labs are
            # inconsistent about capitalising analyte names.
            lowered = layer.casefold()
            name_at = lowered.find(test_name.casefold())
            if name_at < 0:
                continue

        # Look for the value only *after* its own name, and only within a
        # window. A whole-page search would happily match the same digits in an
        # unrelated row two hundred characters away.
        window_end = min(len(layer), name_at + len(test_name) + 200)
        value_at = layer.find(value_raw, name_at, window_end)
        if value_at < 0:
            continue

        span_id = await find_span_for_offset(
            session,
            document_id=document_id,
            page_no=page.page_no,
            char_offset=value_at,
        )
        # The page is right even when no span encloses the offset -- a scanned
        # page may have coarse spans -- so the page number is still worth
        # returning. A clinician can be shown the page without the highlight.
        return (span_id, page.page_no)

    return (None, None)

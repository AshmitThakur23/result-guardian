"""Turning a guideline into retrievable chunks. Phase 8.1.

> Chunking: **section-aware, 400–600 tokens, 15 % overlap, never split a table row**

## Why section-aware, and why overlap

A guideline's meaning lives in its sections. A chunk that starts mid-sentence in
§4.1 and ends mid-sentence in §4.3 retrieves for queries about both and answers
neither, and a citation from it cannot honestly name where it came from.

The 15 % overlap exists for the sentence that straddles a boundary. Without it,
a rule split across two chunks is retrievable from neither, because each half
reads as a fragment.

## Why table rows are never split

A row is `Ceftriaxone | 12 % | R`. Cut it in half and one chunk says
`Ceftriaxone | 12 %` — which is not merely incomplete, it is **wrong**, and it
is exactly the kind of thing a model will happily quote as though it were the
whole story. An antibiogram is mostly table rows, and 8.1 calls it the single
highest-value local document.

## ⛔ No patient data, enforced rather than requested

8.1: *"**No patient data ever enters this store** — enforce with a review step
at ingestion."* :func:`scan_for_identifiers` is that step. It is deliberately
crude and over-sensitive: a false positive costs someone a glance, and a false
negative puts a patient's MRN into a store that is searched, quoted and shown to
other people's clinicians.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: 8.1's window, in approximate tokens. Counted as words x 1.3, which is close
#: enough for chunking and needs no tokeniser — a dependency this module would
#: otherwise pull in for a rounding decision.
TARGET_TOKENS = 500
MIN_TOKENS = 400
MAX_TOKENS = 600
OVERLAP_RATIO = 0.15

#: Headings a guideline actually uses, plus numbered sections.
_HEADING = re.compile(
    r"^\s*(?:"
    r"(?:\d+(?:\.\d+)*\.?\s+[A-Z][^\n]{3,80})"  # 4.2 Empirical therapy
    r"|(?:[A-Z][A-Z \-]{4,60})"  # EMPIRICAL THERAPY
    r"|(?:(?:Section|Chapter|Appendix)\s+[\dIVX]+[^\n]{0,60})"
    r")\s*$",
    re.M,
)

#: A line with two or more column separators is a table row.
_TABLE_ROW = re.compile(r"^[^\n|]*(\|[^\n|]*){2,}$|^\S+(\s{2,}\S+){2,}$")

#: Patterns that mean "this is about a person, not a policy". Crude on purpose.
_IDENTIFIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mrn", re.compile(r"\b(MRN|UHID|IP\s?No|Reg\.?\s?No)\s*[:#-]?\s*\w+", re.I)),
    ("aadhaar", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("phone", re.compile(r"\b(?:\+91[\-\s]?)?[6-9]\d{9}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("dob", re.compile(r"\b(DOB|Date of Birth)\s*[:#-]?\s*\d", re.I)),
    ("patient_label", re.compile(r"\bPatient\s*(Name)?\s*[:#-]\s*\w", re.I)),
    ("accession", re.compile(r"\b(Accession|Specimen|Sample)\s*(No|ID)\s*[:#-]", re.I)),
)


@dataclasses.dataclass(frozen=True)
class Chunk:
    text: str
    section_path: str | None
    char_start: int
    char_end: int
    token_count: int


@dataclasses.dataclass(frozen=True)
class IdentifierHit:
    kind: str
    excerpt: str
    at: int


def approx_tokens(value: str) -> int:
    """Words x 1.3. Close enough to choose a boundary, and needs no tokeniser."""
    return int(len(value.split()) * 1.3)


def scan_for_identifiers(document_text: str) -> list[IdentifierHit]:
    """Find anything that looks like it belongs to a person.

    **Over-sensitive by design.** A false positive costs an admin one glance at
    a highlighted line. A false negative puts an MRN into a store that is
    searched, quoted, and shown to clinicians treating other patients — and
    nothing downstream would ever flag it, because by then it is just text in a
    guideline.
    """
    hits: list[IdentifierHit] = []
    for kind, pattern in _IDENTIFIER_PATTERNS:
        for match in pattern.finditer(document_text):
            start = max(0, match.start() - 30)
            hits.append(
                IdentifierHit(
                    kind=kind,
                    excerpt=document_text[start : match.end() + 30].replace("\n", " "),
                    at=match.start(),
                )
            )
    return hits


def _split_sections(document_text: str) -> list[tuple[str | None, int, int]]:
    """``(section_path, start, end)`` spans. One entry when there are no headings."""
    matches = list(_HEADING.finditer(document_text))
    if not matches:
        return [(None, 0, len(document_text))]

    spans: list[tuple[str | None, int, int]] = []
    if matches[0].start() > 0:
        spans.append((None, 0, matches[0].start()))
    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(document_text)
        )
        spans.append((match.group(0).strip(), match.start(), end))
    return spans


def chunk_document(document_text: str) -> list[Chunk]:
    """Section-aware chunks of 400–600 tokens with 15 % overlap.

    Lines are the atom, not sentences: a table row is a line, and keeping lines
    whole is what stops ``Ceftriaxone | 12 % | R`` being cut into a half-row
    that reads as complete and is wrong.
    """
    chunks: list[Chunk] = []

    for section_path, section_start, section_end in _split_sections(document_text):
        body = document_text[section_start:section_end]
        lines = body.splitlines(keepends=True)

        buffer: list[str] = []
        buffer_tokens = 0
        offset = section_start

        # `section` is bound as a default rather than closed over. The closure
        # is only ever called inside this iteration today, so the difference is
        # invisible -- but a later refactor that deferred the call would label
        # a chunk with whichever section the loop had reached, and a citation
        # naming the wrong part of a guideline is worse than no citation.
        def flush(force: bool = False, section: str | None = section_path) -> None:
            nonlocal buffer, buffer_tokens, offset
            if not buffer:
                return
            joined = "".join(buffer)
            if not force and approx_tokens(joined) < MIN_TOKENS:
                return
            stripped = joined.strip()
            if stripped:
                chunks.append(
                    Chunk(
                        text=stripped,
                        section_path=section,
                        char_start=offset,
                        char_end=offset + len(joined),
                        token_count=approx_tokens(stripped),
                    )
                )
            # 15 % overlap: carry the tail forward so a rule straddling the
            # boundary is retrievable from the next chunk as well as this one.
            keep = max(1, int(len(buffer) * OVERLAP_RATIO))
            carried = buffer[-keep:]
            offset += len(joined) - len("".join(carried))
            buffer = list(carried)
            buffer_tokens = approx_tokens("".join(buffer))

        for line in lines:
            line_tokens = approx_tokens(line)
            # A table row is never split, even when it overshoots the window:
            # half a row is worse than a slightly oversized chunk.
            is_row = bool(_TABLE_ROW.match(line.strip()))
            if buffer_tokens + line_tokens > MAX_TOKENS and buffer and not is_row:
                flush(force=True)
            buffer.append(line)
            buffer_tokens += line_tokens
            if buffer_tokens >= TARGET_TOKENS and not is_row:
                flush()

        flush(force=True)

    return chunks


async def ingest_document(
    session: AsyncSession,
    *,
    title: str,
    publisher: str,
    doc_type: str,
    document_text: str,
    version: str = "1",
    source_ref: str | None = None,
    approved_by: uuid.UUID | None = None,
) -> tuple[uuid.UUID, int, list[IdentifierHit]]:
    """Store a document and its chunks. Returns ``(id, chunk_count, warnings)``.

    ``approved_by`` left ``None`` stores the document **unapproved**, which
    means retrieval cannot see it. That is the safe default: a document becomes
    visible because somebody approved it, never because somebody uploaded it.

    Identifier hits are **returned rather than raised**. Refusing outright would
    be wrong — a guideline may legitimately contain the string "Patient:" in an
    example — so the decision goes to the person approving it, with the
    offending lines in hand.
    """
    warnings = scan_for_identifiers(document_text)

    doc_id = (
        await session.execute(
            text(
                "INSERT INTO kb_documents "
                "  (id, title, publisher, doc_type, version, source_ref, sha256, "
                "   approved_by, approved_at, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :title, :pub, :dtype, :ver, :src, "
                "        :sha, CAST(:by AS uuid), "
                "        CASE WHEN :by IS NULL THEN NULL ELSE now() END, "
                "        now(), now()) "
                "RETURNING id"
            ),
            {
                "title": title,
                "pub": publisher,
                "dtype": doc_type,
                "ver": version,
                "src": source_ref,
                "sha": hashlib.sha256(document_text.encode("utf-8")).hexdigest(),
                "by": str(approved_by) if approved_by else None,
            },
        )
    ).scalar_one()

    chunks = chunk_document(document_text)
    for chunk in chunks:
        await session.execute(
            text(
                "INSERT INTO kb_chunks "
                "  (id, kb_document_id, section_path, char_start, char_end, "
                "   chunk_text, token_count, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :doc, :sec, :start, :end, :txt, "
                "        :tok, now(), now())"
            ),
            {
                "doc": doc_id,
                "sec": chunk.section_path,
                "start": chunk.char_start,
                "end": chunk.char_end,
                "txt": chunk.text,
                "tok": chunk.token_count,
            },
        )

    return (doc_id, len(chunks), warnings)

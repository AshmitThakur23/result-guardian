"""Phase 6.1 — content-addressed storage, MIME sniffing, and the virus hook.

No database. Every property here is about bytes and paths.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.documents import intake, scan
from app.services.documents.storage import DocumentStore, StorageError
from tests import _documents


# ── content-addressed storage ─────────────────────────────────────
def test_the_same_bytes_land_on_the_same_path(tmp_path):
    """6.1: sha256 unique — *"free deduplication"*.

    Free because it falls out of content addressing, rather than needing a
    comparison pass over every document the hospital has ever received.
    """
    store = DocumentStore(tmp_path)
    data = _documents.native_pdf()

    first = store.put_bytes(data)
    second = store.put_bytes(data)

    assert first.sha256 == second.sha256
    assert first.relative_path == second.relative_path
    assert first.deduplicated is False
    assert second.deduplicated is True
    # One file on disk, not two.
    assert len(list(tmp_path.rglob("*.pdf"))) == 1


def test_different_bytes_land_on_different_paths(tmp_path):
    store = DocumentStore(tmp_path)

    a = store.put_bytes(_documents.native_pdf(text="Haemoglobin 9.2"))
    b = store.put_bytes(_documents.native_pdf(text="Creatinine 2.8"))

    assert a.sha256 != b.sha256
    assert a.relative_path != b.relative_path


def test_the_file_is_named_after_its_own_hash(tmp_path):
    store = DocumentStore(tmp_path)

    stored = store.put_bytes(_documents.native_pdf())

    assert stored.relative_path.endswith(f"{stored.sha256}.pdf")
    # Fanned out two levels: a single directory holding a hospital-year of
    # reports is a directory nobody can list.
    assert stored.relative_path.startswith(f"{stored.sha256[:2]}/{stored.sha256[2:4]}/")


def test_verify_detects_a_file_that_changed_after_it_was_filed(tmp_path):
    """A document whose bytes no longer match its name is not a document to
    retry — it is an incident."""
    store = DocumentStore(tmp_path)
    stored = store.put_bytes(_documents.native_pdf())
    assert store.verify(stored.relative_path, stored.sha256) is True

    store.absolute(stored.relative_path).write_bytes(b"tampered")

    assert store.verify(stored.relative_path, stored.sha256) is False


def test_a_path_that_escapes_the_root_is_refused(tmp_path):
    """`storage_path` comes out of the database; one SQL injection away, an
    endpoint that serves a file named by a column serves /etc/shadow."""
    store = DocumentStore(tmp_path / "docs")
    (tmp_path / "docs").mkdir()

    with pytest.raises(StorageError, match="escapes"):
        store.absolute("../../../../etc/passwd")


def test_an_empty_file_is_refused(tmp_path):
    store = DocumentStore(tmp_path)

    with pytest.raises(StorageError):
        store.put_bytes(b"")


def test_no_partial_file_is_left_behind_when_a_write_fails(tmp_path, monkeypatch):
    """Atomicity: a correctly-named file with missing bytes is the one failure
    the whole module exists to prevent."""
    import os

    store = DocumentStore(tmp_path)
    real_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        store.put_bytes(_documents.native_pdf())
    monkeypatch.setattr(os, "replace", real_replace)

    # No .part file, and no half-written .pdf under its final name.
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.pdf")) == []


# ── MIME sniffing ─────────────────────────────────────────────────
def test_a_pdf_is_identified_from_its_bytes():
    assert intake.sniff_mime(_documents.native_pdf()) == "application/pdf"


def test_the_extension_is_never_trusted():
    """6.1: **do not trust the extension.**

    Not theoretical: a hospital mail gateway that renames attachments and an
    attacker naming a payload `report.pdf` produce the same wrong answer if
    you read the name.
    """
    png_bytes = _documents.not_a_pdf()

    sniffed = intake.sniff_mime(png_bytes, filename="urgent-result.pdf")

    assert sniffed == "image/png"
    assert sniffed != "application/pdf"


def test_an_executable_named_pdf_is_not_an_accepted_type():
    # ELF header. Nothing about the name should save it.
    sniffed = intake.sniff_mime(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64, "r.pdf")

    assert sniffed not in intake.ACCEPTED_MIME_TYPES


# ── the virus scan hook ───────────────────────────────────────────
def test_no_scanner_configured_is_skipped_never_clean():
    """The distinction this test exists to protect.

    Recording an unscanned file as ``clean`` would let a deployment with no
    scanner produce an audit trail claiming every file was checked — a false
    assurance, which is worse than no scanner at all.
    """
    result = asyncio.run(scan.scan_bytes(b"x", host="", port=3310, timeout_s=1.0))

    assert result.verdict is scan.ScanVerdict.SKIPPED
    assert result.verdict is not scan.ScanVerdict.CLEAN
    assert result.may_process is True


def test_a_configured_but_unreachable_scanner_refuses_the_file_by_default():
    """A scanner that is down is not a scanner that found nothing.

    Failing open on the malware check is the kind of default that gets chosen
    once and regretted during an incident.
    """
    result = asyncio.run(
        # Port 1 on localhost: nothing listens, and the connection is refused
        # immediately rather than hanging.
        scan.scan_bytes(b"x", host="127.0.0.1", port=1, timeout_s=2.0)
    )

    assert result.verdict is scan.ScanVerdict.ERROR
    assert result.may_process is False


def test_an_unreachable_scanner_can_be_explicitly_downgraded():
    result = asyncio.run(
        scan.scan_bytes(b"x", host="127.0.0.1", port=1, timeout_s=2.0, required=False)
    )

    assert result.verdict is scan.ScanVerdict.SKIPPED
    assert result.may_process is True
    # Still not clean, and the reason says so.
    assert "RG_CLAMAV_REQUIRED" in result.detail

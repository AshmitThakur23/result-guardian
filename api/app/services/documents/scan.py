"""Virus scan hook. Phase 6.1.

    Virus scan hook (ClamAV container) **before** processing

Spoken to over ClamAV's own INSTREAM protocol rather than through a client
library, because the protocol is nine lines of socket code and the library
would be a dependency that has to be audited, pinned and kept alive on an
air-gapped hospital server for the rest of the product's life.

**Three outcomes, and the middle one is the point:**

* ``clean`` — the scanner looked and found nothing.
* ``skipped`` — no scanner is deployed. This is *not* ``clean``. Recording it
  as clean would let a deployment with no scanner produce an audit trail
  claiming every file was checked, which is worse than having no scanner at
  all because it is a false assurance.
* ``infected`` / ``error`` — refuse the file.

When a scanner *is* configured but unreachable, the default is to refuse
(``RG_CLAMAV_REQUIRED``). A scanner that is down is not a scanner that found
nothing, and failing open on the malware check is the kind of default that
gets chosen once and regretted during an incident.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from dataclasses import dataclass
from enum import StrEnum

import structlog

log = structlog.get_logger(__name__)

# ClamAV's INSTREAM refuses chunks above StreamMaxLength; 64 KiB is well under
# every default and keeps memory flat.
CHUNK = 64 * 1024


class ScanVerdict(StrEnum):
    CLEAN = "clean"
    INFECTED = "infected"
    # No scanner deployed. Deliberately distinct from CLEAN.
    SKIPPED = "skipped"
    # A scanner is deployed but could not be reached or did not answer.
    ERROR = "error"


@dataclass(frozen=True)
class ScanResult:
    verdict: ScanVerdict
    detail: str = ""

    @property
    def may_process(self) -> bool:
        """Only a clean result, or an explicitly absent scanner, lets a file
        through."""
        return self.verdict in (ScanVerdict.CLEAN, ScanVerdict.SKIPPED)


async def scan_bytes(
    data: bytes,
    *,
    host: str,
    port: int,
    timeout_s: float,
    required: bool = True,
) -> ScanResult:
    """INSTREAM the bytes to clamd and read its verdict."""
    if not host:
        return ScanResult(
            ScanVerdict.SKIPPED, "no virus scanner configured (RG_CLAMAV_HOST is empty)"
        )

    try:
        result = await asyncio.wait_for(_instream(data, host, port), timeout=timeout_s)
    except TimeoutError:
        detail = f"virus scanner at {host}:{port} did not respond in {timeout_s}s"
        log.warning("virus_scan_timeout", host=host, port=port)
        return _unreachable(detail, required)
    except Exception as exc:
        detail = f"virus scanner at {host}:{port} is unreachable: {exc}"
        log.warning("virus_scan_unreachable", host=host, port=port, error=str(exc))
        return _unreachable(detail, required)

    # clamd answers "stream: OK" or "stream: <SIGNATURE> FOUND".
    if result.endswith("OK"):
        return ScanResult(ScanVerdict.CLEAN)
    if result.endswith("FOUND"):
        signature = result.split(":", 1)[-1].replace("FOUND", "").strip()
        log.error("virus_detected", signature=signature)
        return ScanResult(ScanVerdict.INFECTED, f"malware detected: {signature}")
    return ScanResult(ScanVerdict.ERROR, f"unexpected scanner reply: {result!r}")


def _unreachable(detail: str, required: bool) -> ScanResult:
    if required:
        return ScanResult(ScanVerdict.ERROR, detail)
    # Explicitly configured to continue without the scan. Still not CLEAN --
    # nothing looked at this file.
    log.warning("virus_scan_skipped_after_failure", detail=detail)
    return ScanResult(ScanVerdict.SKIPPED, f"{detail} (RG_CLAMAV_REQUIRED is false)")


async def _instream(data: bytes, host: str, port: int) -> str:
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(b"zINSTREAM\0")
        for offset in range(0, len(data), CHUNK):
            chunk = data[offset : offset + CHUNK]
            # Each chunk is a 4-byte network-order length followed by the
            # bytes; a zero-length chunk terminates the stream.
            writer.write(struct.pack("!I", len(chunk)) + chunk)
        writer.write(struct.pack("!I", 0))
        await writer.drain()
        reply = await reader.read(4096)
        return reply.decode("utf-8", errors="replace").strip().strip("\0")
    finally:
        writer.close()
        # The connection may already be gone -- clamd closes its side as soon
        # as it has answered. Nothing to recover from either way.
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def scan_for_settings(data: bytes) -> ScanResult:
    """Scan using the deployment's configured scanner."""
    from app.config import get_settings

    settings = get_settings()
    return await scan_bytes(
        data,
        host=settings.clamav_host,
        port=settings.clamav_port,
        timeout_s=settings.clamav_timeout_s,
        required=settings.clamav_required,
    )

/**
 * A rendered page, optionally with its spans highlighted. Phase 6.4/6.5.
 *
 *     Store rendered page PNG for the UI highlight overlay
 *
 * Two jobs, and the second is the important one:
 *
 * 1. **The overlay** — draw a rectangle over the words a citation refers to,
 *    so a clinician can see *where* on the page a value came from.
 * 2. **The fallback** — show the page beside the manual entry form when
 *    extraction failed, so a clerk can type what the machine could not read.
 *
 * Coordinates arrive in **PDF points** and the image is rendered at some DPI,
 * so every rectangle is positioned as a *percentage* of the page rather than
 * in pixels. That way the overlay stays correct when the image is scaled to
 * fit a phone, a laptop or a ward monitor — which a pixel offset would not.
 */

import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { DocumentSpan } from "../api/types6";

interface Props {
  documentId: string;
  pageNo: number;
  /** Page size in PDF points. Needed to place the overlay. */
  widthPt: number | null;
  heightPt: number | null;
  spans?: DocumentSpan[];
  /** Spans whose text matches this are highlighted; the rest are not drawn. */
  highlight?: string | null;
  className?: string;
}

export function PageImage({
  documentId,
  pageNo,
  widthPt,
  heightPt,
  spans = [],
  highlight = null,
  className = "",
}: Props) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let revoked = false;
    let objectUrl: string | null = null;

    api
      .objectUrl(`/documents/${documentId}/pages/${pageNo}/image`)
      .then((next) => {
        // The effect may have been torn down while the fetch was in flight --
        // a clerk clicking quickly through pages. Revoking immediately rather
        // than setting state avoids both a leak and a React warning.
        if (revoked) {
          URL.revokeObjectURL(next);
          return;
        }
        objectUrl = next;
        setUrl(next);
      })
      .catch(() => setError("This page image could not be loaded."));

    return () => {
      revoked = true;
      // Without this, paging through a forty-page scan leaks forty images for
      // the life of the tab.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, pageNo]);

  if (error) {
    return (
      <div
        className={`flex items-center justify-center rounded border border-dashed border-line bg-surface-sunken p-8 text-sm text-ink-body ${className}`}
      >
        {error}
      </div>
    );
  }

  if (!url) {
    return (
      <div
        className={`flex items-center justify-center rounded border border-line bg-surface-sunken p-8 text-sm text-ink-muted ${className}`}
        role="status"
      >
        Loading page {pageNo}…
      </div>
    );
  }

  const canOverlay = Boolean(widthPt && heightPt && widthPt > 0 && heightPt > 0);
  const drawn = highlight
    ? spans.filter((span) =>
        span.text.toLowerCase().includes(highlight.toLowerCase()),
      )
    : [];

  return (
    <figure className={`relative m-0 ${className}`}>
      <img
        src={url}
        alt={`Page ${pageNo} of the uploaded report`}
        className="w-full rounded border border-line"
      />
      {canOverlay &&
        drawn.map((span, index) => (
          <span
            key={`${span.char_start}-${index}`}
            aria-hidden="true"
            className="pointer-events-none absolute rounded-sm bg-amber-300/40 ring-1 ring-amber-500"
            style={{
              // Percentages, not pixels: the image is scaled to its container
              // and a pixel offset would drift at every width.
              left: `${(span.bbox.x0 / widthPt!) * 100}%`,
              top: `${(span.bbox.y0 / heightPt!) * 100}%`,
              width: `${((span.bbox.x1 - span.bbox.x0) / widthPt!) * 100}%`,
              height: `${((span.bbox.y1 - span.bbox.y0) / heightPt!) * 100}%`,
            }}
          />
        ))}
    </figure>
  );
}

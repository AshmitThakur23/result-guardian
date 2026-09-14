"""Rendering PDF pages to images. Phase 6.4.

    Store rendered page PNG for the UI highlight overlay

Two callers, one function. OCR needs pixels to recognise; the UI needs an
image to draw highlight rectangles over, and 6.5's mandatory fallback needs
*the same* image beside the manual entry form so a clerk can read the page the
machine could not.

That second caller is why rendering happens even for pages OCR fails on. A
page with no text and no image is a dead end — the clerk has nothing to type
*from*. Rendering is therefore attempted before OCR, not after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ★ The longest side of a rendered page, in pixels.
#
# Not a tuning knob — a safety limit, and it was added after a real failure.
# Rendering at a fixed DPI ignores how big the page actually is: an A2 scan,
# or a page a fax gateway has already upscaled, produced a **15.5-megapixel**
# bitmap at 200 DPI. Denoising that took minutes, the worker hit its memory
# limit and died, pgmq redelivered, and the document was retried **16 times**
# — taking the SLA-timer and notification consumers down with it each time.
#
# 4000 is not arbitrary: it is PaddleOCR's own `max_side_limit`. Anything
# larger is resized by the engine before recognition anyway, so every pixel
# above this was pure cost with no accuracy to show for it.
MAX_RENDER_SIDE_PX = 4000

# ★ And a cap on the **total** pixel count, which is the one that actually
# bounds memory.
#
# Capping the longest side alone is not enough: a 2827 x 4000 page is within
# the side limit and is still 11.3 megapixels, and PaddleOCR on that was
# **SIGKILLed by the OOM killer** at a 3 GB container limit. Recognition
# memory tracks total area, not the longest edge.
#
# 4.2 MP is A4 at 200 DPI (1654 x 2339 ≈ 3.87 MP) with a little headroom —
# that is, exactly the resolution 6.4 asks for, on the page size a lab
# actually prints. A physically larger page is rendered at a proportionally
# lower DPI rather than refused.
#
# ⚠️ **That is a real trade-off, stated plainly**: a very large scan is read at
# less than 200 DPI and may come back with lower confidence. It then falls
# under the 0.70 rule and goes to a human — which is the correct outcome, and
# far better than the alternative this replaced, where the page killed the
# worker process and took Phase 2's timers down with it.
MAX_RENDER_PIXELS = 4_200_000

# Shrink the computed zoom very slightly so PyMuPDF's round-up of each pixel
# dimension cannot carry the result back over MAX_RENDER_PIXELS.
ROUNDING_MARGIN = 0.999


@dataclass(frozen=True)
class RenderedPage:
    page_no: int
    png: bytes
    width_px: int
    height_px: int
    # Multiply a pixel coordinate by this to get PDF points. Carried with the
    # image so no caller has to re-derive it from the DPI and get it wrong --
    # which matters more now that the effective DPI is not always the
    # requested one. See `render_page`.
    points_per_pixel: float
    # The DPI actually used, after any downscale. Logged, not decorative: a
    # page read at 90 DPI because it was enormous is a page whose OCR
    # confidence deserves a second look.
    effective_dpi: float


def render_page(page: object, page_no: int, *, dpi: int) -> RenderedPage:
    """Render one already-open PyMuPDF page, bounded by
    :data:`MAX_RENDER_SIDE_PX`."""
    import pymupdf

    # PDF user space is 72 points to the inch, so this is the only scale
    # factor in the file.
    zoom = dpi / 72.0

    rect = page.rect  # type: ignore[attr-defined]

    # Two independent caps, and the tighter one wins. Scale the *zoom* rather
    # than rendering big and resampling: this never allocates the oversized
    # bitmap in the first place, which is the whole point.
    longest_side_px = max(rect.width, rect.height) * zoom
    side_factor = (
        MAX_RENDER_SIDE_PX / longest_side_px
        if longest_side_px > MAX_RENDER_SIDE_PX
        else 1.0
    )
    area_px = rect.width * rect.height * zoom * zoom
    # Area scales with zoom², so the zoom factor is the square root.
    #
    # ROUNDING_MARGIN is not superstition: PyMuPDF rounds each pixel dimension
    # **up**, so a zoom computed to land exactly on the cap produces an image
    # a few thousand pixels over it. Small in itself, but a cap that can be
    # exceeded is not a cap, and this one exists to stop an OOM kill.
    area_factor = (
        (MAX_RENDER_PIXELS / area_px) ** 0.5 * ROUNDING_MARGIN
        if area_px > MAX_RENDER_PIXELS
        else 1.0
    )

    factor = min(side_factor, area_factor)
    if factor < 1.0:
        zoom *= factor
        log.info(
            "page_render_downscaled",
            page_no=page_no,
            requested_dpi=dpi,
            effective_dpi=round(zoom * 72.0, 1),
            limited_by="area" if area_factor < side_factor else "side",
        )

    matrix = pymupdf.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)  # type: ignore[attr-defined]
    return RenderedPage(
        page_no=page_no,
        png=pixmap.tobytes("png"),
        width_px=pixmap.width,
        height_px=pixmap.height,
        # Derived from the zoom actually used, not from `dpi`. Getting this
        # from the requested DPI would put every span on a downscaled page in
        # the wrong place.
        points_per_pixel=1.0 / zoom,
        effective_dpi=zoom * 72.0,
    )


def png_to_array(png: bytes) -> Any:
    """Decode a PNG into the BGR array OpenCV and PaddleOCR expect."""
    import cv2
    import numpy as np

    buffer = np.frombuffer(png, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not decode the rendered page image")
    return image

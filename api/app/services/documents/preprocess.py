"""OpenCV preprocessing for scanned pages. Phase 6.4.

    OpenCV preprocessing: greyscale → deskew (Hough) → denoise → adaptive
    threshold → optional upscale for <200 DPI

The order is the plan's and it is not arbitrary:

* **greyscale first** — everything downstream is single-channel, and doing it
  first makes the rest cheaper.
* **deskew before threshold** — rotating a binarised image resamples hard
  black-and-white edges into grey, which then has to be re-thresholded. Deskew
  on the greyscale and the interpolation has real values to work with.
* **denoise before threshold** — adaptive thresholding amplifies speckle into
  solid black dots that look exactly like punctuation.
* **threshold last** — OCR engines want clean bi-level text.
* **upscale only when the scan is below 200 DPI** — upscaling a good scan adds
  nothing and costs time; upscaling a bad one genuinely helps, because the
  recogniser has a minimum useful x-height.

Every step is individually skippable and individually degradable: if deskew
cannot find lines, the page passes through unrotated rather than failing. A
preprocessing step that can fail the document would make image tidying a
safety dependency, which it must never be.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Below this, upscaling helps the recogniser; above it, it just costs time.
MIN_USEFUL_DPI = 200

# Hough gives one angle per detected line. A page is skewed, not curved, so a
# sane rotation is small -- anything larger is a mis-detection (a table border,
# a page edge) and rotating by it would make the page worse.
MAX_DESKEW_DEGREES = 15.0

# Below this, rotating costs an interpolation pass and gains nothing visible.
MIN_DESKEW_DEGREES = 0.1

# Above this many pixels, non-local-means denoising is replaced by a median
# blur. ~8.3 MP is a 4000x2080 page — comfortably above anything `render.py`
# now produces, so this is a backstop for images handed in from elsewhere
# rather than the normal path.
MAX_DENOISE_PIXELS = 8_300_000


@dataclass(frozen=True)
class Preprocessed:
    image: Any  # numpy.ndarray, single channel
    deskew_degrees: float
    upscaled: bool


def estimate_skew(gray: Any) -> float:
    """Skew angle in degrees, positive meaning the page leans clockwise.

    Hough on the *edges* of the text, not the text itself: a line of type is a
    horizontal edge once you run Canny over it, and a page of them gives a
    strong consensus angle. Returns 0.0 when there is no consensus, which is
    the right answer for a page with too little text to tell.
    """
    import cv2
    import numpy as np

    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 360,
        threshold=100,
        minLineLength=max(gray.shape[1] // 4, 50),
        maxLineGap=20,
    )
    if lines is None or len(lines) == 0:
        return 0.0

    angles: list[float] = []
    for line in lines[:, 0]:
        x1, y1, x2, y2 = (float(v) for v in line)
        if x2 == x1:
            continue
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # Only near-horizontal lines say anything about skew. Vertical rules
        # in a table are 90° away and would drag a mean into nonsense.
        if abs(angle) <= MAX_DESKEW_DEGREES:
            angles.append(float(angle))

    if not angles:
        return 0.0
    # Median, not mean: one long mis-detected diagonal should not move the
    # answer, and with a median it cannot.
    return float(np.median(angles))


def deskew(gray: Any, angle: float) -> Any:
    import cv2

    if abs(angle) < MIN_DESKEW_DEGREES:
        return gray
    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def preprocess(image: Any, *, dpi: int) -> Preprocessed:
    """Run the whole chain. ``image`` is BGR or greyscale; output is greyscale."""
    import cv2

    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    angle = 0.0
    try:
        angle = estimate_skew(gray)
        gray = deskew(gray, angle)
    except Exception as exc:
        # A page that cannot be deskewed is still a page that can be read.
        log.info("deskew_failed", error=str(exc))
        angle = 0.0

    # Non-local-means: much better than a blur at leaving letter strokes alone
    # while removing scanner speckle — and **much** slower. It compares every
    # pixel's neighbourhood against a search window, so cost grows with the
    # pixel count times the window area.
    #
    # ⚠️ Above MAX_DENOISE_PIXELS it is replaced by a median blur, and that is
    # not a micro-optimisation. On a 15.5-megapixel page this call ran for
    # minutes and drove the worker into its memory limit; the document was
    # redelivered sixteen times and took the SLA-timer and notification
    # consumers down with it on every restart. A median blur removes
    # salt-and-pepper scanner noise, costs almost nothing, and — crucially —
    # finishes. `render.py` now caps rendered pages so this branch should be
    # rare, but a caller can still hand us a large image directly, and the
    # slow path must not be the one that can hang.
    try:
        if gray.size > MAX_DENOISE_PIXELS:
            log.info("denoise_downgraded_to_median", pixels=int(gray.size))
            gray = cv2.medianBlur(gray, 3)
        else:
            gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    except Exception as exc:  # pragma: no cover - defensive
        log.info("denoise_failed", error=str(exc))

    upscaled = False
    if dpi < MIN_USEFUL_DPI:
        scale = MIN_USEFUL_DPI / float(dpi)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        upscaled = True

    # Adaptive, not global: a scan with a shadow down one edge has no single
    # correct threshold, and Otsu would sacrifice one side of the page to get
    # the other right.
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=15,
    )

    return Preprocessed(image=binary, deskew_degrees=angle, upscaled=upscaled)

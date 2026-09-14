"""The scanned path. Phase 6.4.

    - PaddleOCR with detection + recognition, English model; add **Hindi
      model** if local labs print bilingual
    - Keep per-word confidence; **page mean confidence < 0.70 → route to
      `needs_review` instead of guessing**
    - Store rendered page PNG for the UI highlight overlay

**The engine sits behind a boundary, and that is not gold-plating.** THE ONE
RULE says a later phase must never break an earlier one, and Phase 6 is the
phase that introduces a heavy, platform-sensitive native dependency into a
system that had none. If PaddleOCR is absent, fails to load its models, or
throws on a particular page, the correct outcome is *this page goes to a human
with its image attached* — the Phase 3 manual entry workflow, which worked
before Phase 6 existed and still works. The wrong outcome is an ingestion
worker that crash-loops, because that stalls every document including the ones
it could have read.

So :func:`ocr_page` never raises for an engine problem. It returns a page with
no text and a reason, and the pipeline routes it. The only thing OCR can do to
this system is fail to help.

**Confidence is not decoration.** Every word carries the recogniser's own
score and the page mean decides whether anyone is allowed to believe the page.
Below 0.70 the engine is guessing, and a guessed lab value is worse than a
blank one: the blank goes to a human, the guess goes into a chart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.services.documents.native import Span
from app.services.documents.preprocess import preprocess

log = structlog.get_logger(__name__)

# Languages to load. "en" always; add "hi" where labs print bilingual, per the
# plan. Configurable rather than hardcoded because it is exactly the kind of
# local fact CLAUDE.md says must not live in code -- but it is a *deployment*
# fact (which model files to load at boot), so it comes from the environment
# rather than from a table that would need a restart to take effect anyway.
DEFAULT_LANG = "en"


class OcrUnavailableError(RuntimeError):
    """The engine is not installed or could not be initialised.

    Caught inside this module. It exists so the reason can be carried to the
    caller as text rather than as a bare None.
    """


@dataclass
class OcrPage:
    page_no: int
    text: str
    spans: list[Span] = field(default_factory=list)
    # Mean of the per-word confidences, or None when nothing was recognised.
    # None is not 0.0: "we read nothing" and "we read something we do not
    # believe" are different, and only the second is a quality problem.
    mean_confidence: float | None = None
    # Set when the page could not be OCRed at all. The pipeline turns this
    # into `needs_review` plus a message a human can act on.
    unavailable_reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.unavailable_reason is None and bool(self.text.strip())


_engine: Any = None
_engine_error: str | None = None


def _get_engine(lang: str) -> Any:
    """Load PaddleOCR once per process, and remember a failure as a failure.

    Model loading takes seconds and allocates hundreds of megabytes; doing it
    per page would make a 40-page scan unusable. Caching the *error* matters
    just as much — without it, a missing engine means every page pays a full
    import-and-fail before being routed to review.
    """
    global _engine, _engine_error
    if _engine is not None:
        return _engine
    if _engine_error is not None:
        raise OcrUnavailableError(_engine_error)

    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        _engine_error = f"PaddleOCR is not installed: {exc}"
        log.warning("ocr_engine_unavailable", error=str(exc))
        raise OcrUnavailableError(_engine_error) from exc

    # Angle classification on: scanned pages arrive upside down often enough
    # that the alternative is a stack of blank results.
    #
    # PaddleOCR renamed these keywords between 2.x and 3.x, so both spellings
    # are tried. **3.x is tried first**, because it is what is pinned.
    #
    # ⚠️ Note `ValueError` in the catch, not just `TypeError`. PaddleOCR 3.x
    # validates keywords itself and raises `ValueError: Unknown argument:
    # show_log` — so a `TypeError`-only fallback never ran, the generic
    # handler below cached "unavailable", and **OCR silently never worked at
    # all**. The pipeline degraded exactly as designed (page images rendered,
    # document routed to review), which is precisely why nothing looked
    # broken. Found by the end-to-end run, not by any unit test: the unit
    # tests stub the engine out.
    #
    # `enable_mkldnn=False` is load-bearing, not a tuning knob. Paddle's
    # oneDNN CPU backend cannot load the PP-OCRv6 recognition model —
    # `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
    # [pir::ArrayAttribute<pir::DoubleAttribute>]`, raised from
    # onednn_instruction.cc at *inference* time, not at load. With oneDNN on,
    # the engine constructs happily and then fails on every single page.
    # Slower, and it works. Found by running it.
    #
    # Document orientation and unwarping are off for the same class of reason
    # plus a clinical one: they are two extra models to load and two more
    # ways to fail, and our pages come from `render_page` at a known
    # orientation. OpenCV deskew in `preprocess` already handles the tilt a
    # flatbed scanner introduces.
    attempts: tuple[dict[str, object], ...] = (
        {
            "lang": lang,
            "use_textline_orientation": True,
            "enable_mkldnn": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
        },
        {"use_textline_orientation": True, "lang": lang},
        {"use_angle_cls": True, "lang": lang, "show_log": False},
    )
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            _engine = PaddleOCR(**kwargs)
            break
        except (TypeError, ValueError) as exc:
            # Wrong keyword spelling for this version. Try the other one.
            last_error = exc
            continue
        except Exception as exc:
            _engine_error = f"PaddleOCR failed to initialise: {exc}"
            log.warning("ocr_engine_init_failed", error=str(exc))
            raise OcrUnavailableError(_engine_error) from exc

    if _engine is None:
        _engine_error = f"PaddleOCR failed to initialise: {last_error}"
        log.warning("ocr_engine_init_failed", error=str(last_error))
        raise OcrUnavailableError(_engine_error)

    log.info("ocr_engine_ready", lang=lang)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine and cached error. For tests."""
    global _engine, _engine_error
    _engine = None
    _engine_error = None


def _as_three_channel(image: Any) -> Any:
    """Give PaddleOCR the BGR array it insists on.

    A greyscale or binarised page is single-channel, and passing one through
    produces an ``IndexError`` from inside the library rather than a usable
    complaint. Converting here rather than in ``preprocess`` keeps the
    preprocessing chain honest: its output genuinely is one channel, and this
    is the engine's requirement, not the image's nature.
    """
    import cv2

    if getattr(image, "ndim", 3) == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    return image


def _bbox_points(
    x0: float, y0: float, x1: float, y1: float, scale: float
) -> dict[str, float]:
    """Pixel coordinates → PDF points, in the shape the span table wants.

    Converting here means a span from a scanned page is in the same coordinate
    system as one from a native page, so the overlay never has to know which
    path produced it.
    """
    return {
        "x0": round(x0 * scale, 2),
        "y0": round(y0 * scale, 2),
        "x1": round(x1 * scale, 2),
        "y1": round(y1 * scale, 2),
    }


def _quad_to_bbox(quad: Any) -> dict[str, float]:
    """PaddleOCR returns a rotated quadrilateral; the overlay wants a rect.

    The axis-aligned bounding box of the quad is a superset of the text, which
    is the safe direction for a highlight: it may include a little whitespace,
    it will never exclude part of the value being cited.
    """
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return {
        "x0": round(min(xs), 2),
        "y0": round(min(ys), 2),
        "x1": round(max(xs), 2),
        "y1": round(max(ys), 2),
    }


def _iter_results(raw: Any) -> list[tuple[Any, str, float]]:
    """Normalise PaddleOCR's output across its two output shapes.

    2.x returns ``[[ [quad, (text, score)], ... ]]``; 3.x returns a list of
    dicts with parallel ``rec_texts`` / ``rec_scores`` / ``rec_polys`` arrays.
    Reading both here keeps the version difference out of the caller.
    """
    out: list[tuple[Any, str, float]] = []
    if not raw:
        return out

    first = raw[0] if isinstance(raw, list) else raw

    # 3.x dict form.
    if isinstance(first, dict):
        texts = first.get("rec_texts") or []
        scores = first.get("rec_scores") or []
        polys = first.get("rec_polys") or first.get("dt_polys") or []
        for i, text in enumerate(texts):
            score = float(scores[i]) if i < len(scores) else 0.0
            quad = polys[i] if i < len(polys) else [(0, 0), (0, 0), (0, 0), (0, 0)]
            out.append((quad, str(text), score))
        return out

    # 2.x nested-list form.
    for line in first or []:
        try:
            quad, (text, score) = line[0], line[1]
        except Exception:  # pragma: no cover - unexpected shape
            continue
        out.append((quad, str(text), float(score)))
    return out


def ocr_page(
    image: Any,
    page_no: int,
    *,
    dpi: int,
    lang: str = DEFAULT_LANG,
    scale_to_pdf: float = 1.0,
) -> OcrPage:
    """OCR one rendered page image. **Never raises for an engine problem.**

    ``scale_to_pdf`` converts pixel coordinates back into PDF points, so a
    span from a scanned page is in the same coordinate system as a span from a
    native page. Without it the overlay would need to know which path produced
    each span, and every consumer would have to get that right.
    """
    try:
        engine = _get_engine(lang)
    except OcrUnavailableError as exc:
        return OcrPage(page_no=page_no, text="", unavailable_reason=str(exc))

    try:
        prepared = preprocess(image, dpi=dpi)
    except Exception as exc:
        # Preprocessing is an optimisation. If it fails, OCR the raw render
        # rather than losing the page -- a slightly skewed page still reads.
        log.info("preprocess_failed_using_raw", page_no=page_no, error=str(exc))
        prepared_image = image
        upscale_factor = 1.0
    else:
        prepared_image = prepared.image
        upscale_factor = (
            float(prepared.image.shape[0]) / float(image.shape[0])
            if image.shape[0]
            else 1.0
        )

    # ⚠️ PaddleOCR requires three channels. `preprocess` returns a binarised
    # single-channel image -- which is exactly what an OCR engine wants
    # conceptually, and which PaddleOCR rejects with a bare `IndexError:
    # tuple index out of range` from deep inside its own preprocessing. Not a
    # message anyone would trace back to channel count, and it produced a
    # silently blank page rather than an error. Found by probing the engine
    # directly.
    prepared_image = _as_three_channel(prepared_image)

    try:
        raw = _run(engine, prepared_image)
    except Exception as exc:
        log.warning("ocr_failed", page_no=page_no, error=str(exc))
        return OcrPage(
            page_no=page_no, text="", unavailable_reason=f"OCR failed: {exc}"
        )

    results = _iter_results(raw)
    if not results:
        # A genuinely blank page. Not an error, and not low confidence -- there
        # was nothing to be confident about.
        return OcrPage(page_no=page_no, text="", mean_confidence=None)

    # Reading order: top to bottom, then left to right. OCR detection boxes
    # come back in the detector's own order, which is not reading order.
    results.sort(
        key=lambda r: (
            round(min(float(p[1]) for p in r[0]), 1),
            min(float(p[0]) for p in r[0]),
        )
    )

    parts: list[str] = []
    spans: list[Span] = []
    scores: list[float] = []
    cursor = 0

    # Pixels -> PDF points, undoing any upscale preprocessing applied.
    to_points = scale_to_pdf / upscale_factor if upscale_factor else scale_to_pdf

    for quad, line_text, score in results:
        if not line_text.strip():
            continue
        if parts:
            parts.append("\n")
            cursor += 1

        bbox = _quad_to_bbox(quad)
        line_x0, line_x1 = bbox["x0"], bbox["x1"]
        line_width = max(line_x1 - line_x0, 1e-6)

        # One span per word, interpolated across the recognised line -- the
        # same contract native.py provides, so a consumer never has to ask
        # which path produced a span.
        offset_in_line = 0
        first_word = True
        for word in line_text.split():
            start_in_line = line_text.find(word, offset_in_line)
            if start_in_line < 0:  # pragma: no cover
                start_in_line = offset_in_line
            offset_in_line = start_in_line + len(word)

            if not first_word:
                parts.append(" ")
                cursor += 1
            first_word = False

            word_start = cursor
            parts.append(word)
            cursor += len(word)

            ratio_start = start_in_line / max(len(line_text), 1)
            ratio_end = offset_in_line / max(len(line_text), 1)
            spans.append(
                Span(
                    page_no=page_no,
                    char_start=word_start,
                    char_end=cursor,
                    bbox=_bbox_points(
                        line_x0 + line_width * ratio_start,
                        bbox["y0"],
                        line_x0 + line_width * ratio_end,
                        bbox["y1"],
                        to_points,
                    ),
                    text=word,
                )
            )
        scores.append(float(score))

    text = "".join(parts)
    mean = sum(scores) / len(scores) if scores else None

    log.info(
        "page_ocred",
        page_no=page_no,
        words=len(spans),
        mean_confidence=round(mean, 3) if mean is not None else None,
    )
    return OcrPage(page_no=page_no, text=text, spans=spans, mean_confidence=mean)


def _run(engine: Any, image: Any) -> Any:
    """Call whichever inference method this PaddleOCR version exposes."""
    predict = getattr(engine, "predict", None)
    if predict is not None:
        try:
            return predict(image)
        except TypeError:  # pragma: no cover - signature drift
            pass
    return engine.ocr(image)

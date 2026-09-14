"""Asking NODE B to phrase what NODE A retrieved. Phase 8.4.

The model's job is **wording**, not knowing. It is given chunks that NODE A
already found and asked to explain them in two sentences with a quote from each.
Everything it says is then checked by 8.5 against the chunks it was given, and
anything it invented is thrown away.

## Three things measured on the real NODE B before this was written

1. **`think: false` does not stop `qwen3` reasoning.** Neither endpoint honours
   it, and the `/no_think` prefix returns *empty content*, which is worse.

2. **The model closes the think block without opening it.** A real response ends
   ``…what they asked for.\\n</think>\\n\\nOK`` — a closing tag and no opening one,
   so the `<think>.*?</think>` regex every tutorial uses matches **nothing** and
   the monologue lands in the UI. :func:`visible_text` takes everything after the
   **last** closing tag instead.

3. **Reasoning is ~90 % of the output.** A realistic request took 21.9 s of a
   30 s budget, producing 2,361 characters of thinking for 222 of answer. So the
   token budget is sized for the monologue, not the reply — a `num_predict`
   sized for the answer truncates mid-thought and returns nothing usable.

## Failure is always silence, never a guess

Every error path returns ``None``. A timeout, a refusal, malformed JSON, an
unreachable node — all of them mean *no explanation shown*, and the flag beneath
it is untouched. The explanation is a convenience; the flag is the product.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

#: Sized for reasoning plus the answer. See point 3 above — the smaller values
#: tried (16 and 64) truncated mid-monologue and returned nothing at all.
NUM_PREDICT = 2048

#: Temperature 0, per 8.4. This is a phrasing task with a right answer; sampling
#: would make the same case explain differently on two viewings.
TEMPERATURE = 0.0

#: Measured, not guessed: `mistral:7b` returned a complete answer with a
#: verbatim quote in **17.5 s** on 2026-09-15. 60 s leaves room for a longer
#: source or a busier GPU without being so generous that a wedged request holds
#: a clinician's screen. Exceeding it is not an error — it is "no explanation
#: this time", and the flag underneath is untouched.
TIMEOUT_S = 60.0

_SYSTEM = (
    "You explain laboratory and radiology findings to doctors, using ONLY the "
    "numbered sources given to you.\n"
    "Rules you must not break:\n"
    "1. Every claim must come from a source. Do not add facts, thresholds, "
    "drug names or figures that are not in the sources.\n"
    "2. Quote the source text you used, VERBATIM, copying at least 20 "
    "characters exactly as written.\n"
    "3. If the sources do not answer the question, say so and cite nothing.\n"
    "4. Reply with JSON only, matching this shape:\n"
    '{"explanation": "<two sentences>", '
    '"evidence": [{"source": <number>, "quote": "<verbatim text>"}]}'
)


@dataclasses.dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    text: str
    title: str


@dataclasses.dataclass(frozen=True)
class Generated:
    explanation: str
    #: ``(chunk_id, quoted_text)`` pairs, unverified. 8.5 decides what survives.
    evidence: list[tuple[str, str]]
    elapsed_s: float
    #: Kept so a rejection can be investigated against what the model actually
    #: returned rather than against a reconstruction of it.
    raw: str


def visible_text(content: str) -> str:
    """Strip `qwen3`'s reasoning. **`rsplit`, not a paired-tag regex.**

    The model emits `</think>` with no `<think>`, so a regex looking for a
    matched pair silently matches nothing and passes the entire monologue
    through. Taking everything after the last closing tag is what works, and
    was verified against the live model.
    """
    if "</think>" in content:
        return content.rsplit("</think>", 1)[1].strip()
    return content.strip()


def parse_response(raw: str) -> Generated | None:
    """Pull the JSON object out of the model's reply. ``None`` if it is not there.

    The model wraps JSON in prose, in fences, or in nothing, depending on its
    mood. Finding the outermost braces is more robust than trusting `format:
    json`, which 8.4 notes is untested against a model that reasons in prose.
    """
    text = visible_text(raw)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    explanation = str(payload.get("explanation") or "").strip()
    if not explanation:
        return None

    evidence: list[tuple[str, str]] = []
    for item in payload.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        source = item.get("source")
        if quote and source is not None:
            evidence.append((str(source), quote))

    return Generated(explanation=explanation, evidence=evidence, elapsed_s=0.0, raw=raw)


async def generate(
    *,
    base_url: str,
    model: str,
    question: str,
    sources: list[SourceChunk],
    timeout_s: float = TIMEOUT_S,
) -> Generated | None:
    """Ask NODE B. Returns ``None`` on **any** failure, including a slow one.

    Sources are numbered in the prompt and the model cites by number, which is
    then mapped back to a chunk id here. Asking it to echo a UUID would invite
    it to invent one, and a fabricated identifier is harder to spot than a
    fabricated quote.
    """
    if not sources:
        return None

    numbered = "\n\n".join(
        f"[{i}] ({chunk.title})\n{chunk.text}" for i, chunk in enumerate(sources, 1)
    )
    prompt = f"{question}\n\nSOURCES:\n{numbered}"

    import time

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "think": False,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    "options": {
                        "num_predict": NUM_PREDICT,
                        "temperature": TEMPERATURE,
                    },
                },
            )
            response.raise_for_status()
            raw = (response.json().get("message") or {}).get("content", "")
    except Exception as exc:
        log.info("llm_generation_unavailable", error=type(exc).__name__)
        return None

    elapsed = time.perf_counter() - started
    parsed = parse_response(raw)
    if parsed is None:
        log.info("llm_response_malformed", elapsed_s=round(elapsed, 1))
        return None

    # Map the source numbers back to chunk ids. A number the model invented --
    # "[7]" when four sources were given -- is dropped rather than guessed at.
    resolved: list[tuple[str, str]] = []
    for source_no, quote in parsed.evidence:
        try:
            index = int(re.sub(r"\D", "", source_no)) - 1
        except ValueError:
            continue
        if 0 <= index < len(sources):
            resolved.append((sources[index].chunk_id, quote))

    return Generated(
        explanation=parsed.explanation,
        evidence=resolved,
        elapsed_s=round(elapsed, 1),
        raw=raw,
    )


def as_dict(generated: Generated) -> dict[str, Any]:
    return {
        "explanation": generated.explanation,
        "evidence_count": len(generated.evidence),
        "elapsed_s": generated.elapsed_s,
    }

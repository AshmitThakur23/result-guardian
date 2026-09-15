/**
 * "Why does this matter?" — with the source, in context. Phase 8.6.
 *
 * ## The design problem this screen actually has
 *
 * Generated clinical text is **more dangerous when it looks trustworthy**. A
 * confident paragraph in a clean panel reads as authoritative whether or not
 * anything behind it is true, so the visual job here is the opposite of the
 * usual one: the citation must be at least as prominent as the prose, and the
 * prose must be visibly *subordinate* to it.
 *
 * Four decisions follow from that, and each is a deliberate refusal of a
 * prettier option:
 *
 * 1. **The quote is highlighted inside its surrounding passage**, not shown
 *    alone. A quote on its own still has to be taken on trust. Reading it in
 *    place is what makes it checkable, and it is the only reason the API
 *    returns `chunk_text` and offsets at all.
 *
 * 2. **The source document is named above the explanation, not under it.**
 *    Provenance first. A reader who stops after the first line should still
 *    know what they are reading came from the hospital's own policy.
 *
 * 3. **The explanation is styled as a quotation, not as a statement.** It is
 *    somebody's paraphrase of a source, and that is what it should look like.
 *
 * 4. **Rejections are always shown, including zero.** "0 rejected" is the
 *    sentence that tells a clinician the checking happened at all. Hiding it
 *    when nothing was rejected would make the check invisible exactly when it
 *    is working.
 *
 * ## Why there is no spinner-with-progress
 *
 * Generation takes ~14 s and the duration is genuinely unknown — a progress bar
 * would be a lie, and one that stalls at 90 % is worse than none. The button
 * states what is happening and how long it usually takes, which is honest and
 * more useful than an animation.
 */

import { useState } from "react";

import { useExplain } from "../api/queries8";
import type { ExplainEvidence } from "../api/types8";
import { Badge } from "./ui/Badge";
import { Banner } from "./ui/Banner";
import { Button } from "./ui/Button";
import { Spinner } from "./ui/States";

/**
 * The passage, with the verified quote highlighted where it sits.
 *
 * Offsets come from the span verifier, which computed them against the
 * *normalised* text — so the same normalised text is rendered here. Slicing the
 * raw text with normalised offsets would highlight the wrong characters, and a
 * highlight that is off by a few characters is worse than none: it looks like a
 * mis-citation.
 */
function QuotedPassage({ evidence }: { evidence: ExplainEvidence }) {
  const { chunk_text, char_start, char_end } = evidence;
  const before = chunk_text.slice(0, char_start);
  const quote = chunk_text.slice(char_start, char_end);
  const after = chunk_text.slice(char_end);

  // The verifier guarantees the offsets are valid, but a defensive fallback
  // costs nothing and means a future change to normalisation degrades to
  // "unhighlighted passage" rather than to a scrambled one.
  if (!quote) {
    return <p className="text-sm leading-relaxed text-ink-body">{chunk_text}</p>;
  }

  return (
    <p className="text-sm leading-relaxed text-ink-body">
      {before}
      {/* Two channels, not one. `ring-brand/30` used to be the only outline and
          it compiled to nothing -- a bare `var(--rg-*)` colour cannot synthesise
          alpha -- so the "highlight" was a background wash alone. The underline
          is the non-colour channel: it survives greyscale printing, a mono
          ward printer, and a reader who cannot distinguish the wash. */}
      <mark
        className="rounded-sm bg-brand-subtle px-0.5 font-medium text-brand-text
                   underline decoration-brand decoration-2 underline-offset-2
                   ring-1 ring-inset ring-brand-line"
      >
        {quote}
      </mark>
      {after}
    </p>
  );
}

function Citation({ evidence, index }: { evidence: ExplainEvidence; index: number }) {
  return (
    <li className="rounded border border-line bg-surface-sunken">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <span
          aria-hidden="true"
          className="flex size-5 shrink-0 items-center justify-center rounded-full
                     bg-brand text-xs font-semibold text-ink-inverse"
        >
          {index + 1}
        </span>
        <span className="min-w-0 text-sm font-medium text-ink">
          {evidence.document_title}
        </span>
        {evidence.section_path ? (
          <span className="text-xs text-ink-muted">§ {evidence.section_path}</span>
        ) : null}
        {evidence.page_no !== null ? (
          <span className="text-xs text-ink-muted">p. {evidence.page_no}</span>
        ) : null}

        {/* A re-typed quote is still a valid citation, but the reader is
            entitled to know it was not copied character for character. */}
        <span className="ml-auto">
          {evidence.match_ratio >= 1 ? (
            <Badge tone="normal">Exact quote</Badge>
          ) : (
            <Badge tone="info">
              {`Matched ${Math.round(evidence.match_ratio * 100)}%`}
            </Badge>
          )}
        </span>
      </div>
      <div className="px-3 py-3">
        <QuotedPassage evidence={evidence} />
      </div>
    </li>
  );
}

export function ExplainPanel({
  caseId,
  query,
}: {
  caseId: string;
  /** Built from structured fields by the caller — never the raw report text. */
  query: string;
}) {
  const explain = useExplain(caseId);
  const [asked, setAsked] = useState(false);
  const [ownQuestion, setOwnQuestion] = useState("");
  const result = explain.data;

  // The default question is built from the structured fields by the caller.
  // A typed one replaces it, but only once it is long enough for the API to
  // accept -- `query` has min_length 3, and a two-character question would be
  // refused by the server for a reason the reader cannot see.
  const effectiveQuery =
    ownQuestion.trim().length >= 3 ? ownQuestion.trim() : query;

  function ask() {
    setAsked(true);
    explain.mutate(effectiveQuery);
  }

  return (
    <section
      aria-labelledby="explain-heading"
      className="rounded border border-line bg-surface"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 id="explain-heading" className="text-sm font-semibold text-ink">
            Why does this matter?
          </h2>
          <p className="mt-0.5 max-w-prose text-sm text-ink-muted">
            Looks up the hospital's approved guidance and explains this result
            against it. <strong className="font-medium text-ink-body">Every
            quotation is checked against its source before you see it.</strong>
          </p>
        </div>
        <Button
          size="sm"
          variant={asked ? "secondary" : "primary"}
          disabled={explain.isPending}
          onClick={ask}
        >
          {explain.isPending ? (
            <>
              <Spinner />
              Reading the guidance…
            </>
          ) : asked ? (
            "Ask again"
          ) : (
            "Explain"
          )}
        </Button>
      </header>

      <div className="space-y-4 px-4 py-4">
        {/* ★ The pre-ask state says what is *about* to happen, in the space it
            was already occupying. One muted sentence in a 200px panel told a
            reader almost nothing; the thing worth being explicit about, before
            anyone presses the button, is the boundary — what gets searched,
            what gets checked, and what does not change. */}
        {!asked && !explain.isPending ? (
          <div className="rounded border border-dashed border-line bg-surface-sunken px-3 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-body">
              What happens when you ask
            </h3>
            <ul className="mt-2 space-y-1 text-sm text-ink-muted">
              <li>Nothing is sent anywhere until you ask.</li>
              <li>
                It searches the hospital's own approved guidance — never this
                patient's report.
              </li>
              <li>
                It quotes that guidance, and every quotation is checked against
                its source before you see it.
              </li>
              <li>It usually takes about 15 seconds.</li>
              <li>
                Nothing about this case changes either way — tracking, timers
                and escalation are unaffected.
              </li>
            </ul>
          </div>
        ) : null}

        {/* ★ Ask something specific.
            The endpoint has always accepted a free-text query; only the UI
            hardcoded one built from the structured fields. Worth being plain
            about what this searches: it searches the hospital's **approved
            guidance**, not this patient's report. A question the guidance does
            not cover returns "no approved guidance", which is the honest
            answer and not a failure.

            Behind a disclosure, because as the first thing in the body it made
            an evidence panel read as a search box — and nobody has asked
            anything yet. Native `<details>`: keyboard-operable and announced by
            screen readers with no JS and no dependency.

            `open` by default is deliberate, not a default left alone. The
            caveat sentence below is the one that draws the line between the
            guidance and the patient's own report, and it is asserted visible on
            the case page by `e2e/phase8-guidance.spec.ts`. A closed disclosure
            hides it — from the test, and from the reader it was written for. */}
        <details open>
          <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-ink-body marker:text-ink-muted">
            Ask something specific instead
          </summary>
          <label className="mt-2 block">
            <span className="text-xs font-medium uppercase tracking-wide text-ink-body">
              Ask something specific{" "}
              <span className="font-normal normal-case text-ink-muted">
                (optional)
              </span>
            </span>
            <div className="mt-1 flex flex-wrap gap-2">
              <input
                value={ownQuestion}
                onChange={(event) => setOwnQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !explain.isPending) ask();
                }}
                maxLength={300}
                placeholder={query}
                className="min-w-[14rem] flex-1 rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink"
              />
              {ownQuestion ? (
                <Button size="sm" variant="secondary" onClick={() => setOwnQuestion("")}>
                  Reset
                </Button>
              ) : null}
            </div>
            <span className="mt-1 block text-xs text-ink-muted">
              Searches the hospital's approved guidance — not this patient's
              report. Leave it blank to use{" "}
              <span className="font-mono">{query}</span>.
            </span>
          </label>
        </details>

        {explain.isPending ? (
          <p className="flex items-center gap-2 text-sm text-ink-muted">
            <Spinner />
            Searching approved guidance, then asking the assist to summarise it.
            Usually about 15 seconds.
          </p>
        ) : null}

        {/* A transport failure is not the same as "no explanation available",
            and saying so stops a network blip reading as a clinical finding. */}
        {explain.isError ? (
          <Banner tone="warning" title="The request did not complete">
            The explanation could not be requested just now. Nothing about this
            case has changed — tracking, timers and escalation are unaffected.
          </Banner>
        ) : null}

        {result && result.explanation === null ? (
          // `info`, never `danger`. All four null paths are normal operating
          // states, and styling them as errors would teach a ward that the
          // system is broken when it is behaving exactly as designed.
          <div className="space-y-4">
            <Banner tone="info" title="No explanation shown">
              <p>{result.note}</p>
              {result.rejected_count > 0 ? (
                <p className="mt-2">
                  {result.rejected_count} quotation
                  {result.rejected_count === 1 ? " was" : "s were"} rejected for
                  not appearing in the source. This is recorded.
                </p>
              ) : null}
            </Banner>

            {/* ★ The degradation that matters. With NODE B off there is no
                paraphrase, but the hospital's own approved guidance was still
                found on NODE A, by plain keyword search, and it is the part a
                clinician actually needs. Sending them away with an apology
                while the answer sits in the knowledge base would be the wrong
                way to fail. */}
            {result.retrieved.length > 0 ? (
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                  The guidance that was found
                </h3>
                <p className="mt-1 text-sm text-ink-muted">
                  Shown exactly as it appears in the source. Nothing here was
                  written or summarised by the assist.
                </p>
                <ul className="mt-2 space-y-3">
                  {result.retrieved.map((passage) => (
                    <li
                      key={passage.chunk_id}
                      className="rounded border border-line bg-surface-sunken"
                    >
                      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
                        <span className="min-w-0 text-sm font-medium text-ink">
                          {passage.document_title}
                        </span>
                        {passage.section_path ? (
                          <span className="text-xs text-ink-muted">
                            § {passage.section_path}
                          </span>
                        ) : null}
                        {passage.page_no !== null ? (
                          <span className="text-xs text-ink-muted">
                            p. {passage.page_no}
                          </span>
                        ) : null}
                      </div>
                      <p className="whitespace-pre-line px-3 py-3 text-sm leading-relaxed text-ink-body">
                        {passage.chunk_text}
                      </p>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {result && result.explanation !== null ? (
          <div className="space-y-4">
            {/* Provenance above the prose: a reader who stops after one line
                should still know where this came from. */}
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="brand" dot>
                {result.evidence.length} verified{" "}
                {result.evidence.length === 1 ? "source" : "sources"}
              </Badge>
              <Badge tone={result.rejected_count > 0 ? "followup" : "normal"}>
                {result.rejected_count} rejected
              </Badge>
              <span className="text-xs text-ink-muted">
                {result.sources_considered} passage
                {result.sources_considered === 1 ? "" : "s"} considered
              </span>
            </div>

            {/* Decision 3 in the header docstring says the prose must be
                visibly *subordinate* to the citation. A left-ruled `text-base`
                blockquote said the opposite: that is pull-quote styling, the
                house style for the most important sentence on a page, and it
                made the generated paraphrase the loudest thing in the panel.

                So: a labelled `<figure>`, caption ABOVE the prose — it has to
                be read before the sentence it qualifies, not after it — and the
                prose itself at `text-sm text-ink-body`, which is
                typographically lighter than the `text-sm font-medium text-ink`
                citation titles beneath it. The reading order and the visual
                weight now agree with the docstring. */}
            <figure className="rounded border border-line bg-surface-sunken px-3 py-3">
              <figcaption className="flex items-start gap-1.5 text-xs text-ink-muted">
                <svg
                  aria-hidden="true"
                  viewBox="0 0 16 16"
                  className="mt-0.5 size-3.5 shrink-0"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="8" cy="8" r="6.25" />
                  <path d="M8 7.25v4" />
                  <path d="M8 4.75h.01" />
                </svg>
                <span>
                  Summarised by the assist from the sources below — not a
                  clinical statement.
                </span>
              </figcaption>
              <p className="mt-2 text-sm leading-relaxed text-ink-body">
                {result.explanation}
              </p>
            </figure>

            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Checked against
              </h3>
              <ul className="mt-2 space-y-3">
                {result.evidence.map((evidence, index) => (
                  <Citation
                    key={`${evidence.chunk_id}-${index}`}
                    evidence={evidence}
                    index={index}
                  />
                ))}
              </ul>
            </div>

          </div>
        ) : null}
      </div>

      {/* 8.6 requires this to be **persistent**, so it sits outside every
          branch above rather than inside the success one. A disclaimer that
          appears only when there is an explanation is absent in exactly the
          states a reader is most likely to misread — and it would be the one
          thing on screen that changed depending on whether the AI was up. */}
      <footer className="border-t border-line px-4 py-3">
        <p className="text-xs text-ink-muted">
          <strong className="font-medium text-ink-body">Information only.</strong>{" "}
          Retrieved from hospital-approved guidelines.{" "}
          <strong className="font-medium text-ink-body">
            The treating doctor decides.
          </strong>
        </p>
      </footer>
    </section>
  );
}

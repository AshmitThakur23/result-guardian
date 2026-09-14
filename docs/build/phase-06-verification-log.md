# Phase 6 — build & verification log

**Working tracker.** Every item Phase 6 needs, what state it is actually in,
and what proved it. Updated as work happens, not at the end.

> **The rule this file exists to enforce** — [`CLAUDE.md`](../../CLAUDE.md):
> *"Never tick something because it was typed. Written ≠ done. If it has not
> run, it is 🟡, not ✅."*
>
> So: **🟡 means the code exists and has never been executed.** It is not a
> softer ✅. Anything still 🟡 at the end of a session is unfinished work, and
> this file is where the next session finds it.

**Legend:** ✅ built and verified · 🟡 written, never run · 🔵 in progress ·
⬜ not started · 🔴 blocked

---

## 1 · Build items

| # | § | Item | State | Proved by |
|---|---|---|---|---|
| 1.1 | 6.1 | `POST /api/reports/upload`, multipart, 25 MB cap | ✅ | `test_phase_6_api.py::test_upload_accepts_a_pdf_and_queues_extraction` · E2E §1 |
| 1.2 | 6.1 | MIME sniffing from bytes, extension ignored | ✅ | `test_the_extension_is_never_trusted` · E2E §6 (ELF named `.pdf` → 422) |
| 1.3 | 6.1 | Watched folder: inbox → processing → archive/Y/M/D | 🟡 | Written; off by default (`RG_WATCHED_FOLDER_ENABLED=false`). **Never executed.** |
| 1.4 | 6.1 | Virus scan hook (ClamAV INSTREAM) before processing | 🟡 | `skipped`/`error` paths tested (`test_phase_6_storage_intake.py`). **The `clean` path has never run against a real clamd** — no scanner deployed here. |
| 1.5 | 6.1 | Content-addressed `sha256.pdf` storage, DB holds path | ✅ | `test_the_same_bytes_land_on_the_same_path` · E2E §2 |
| 1.6 | 6.1 | `documents` / `document_pages` / `document_spans` tables | ✅ | Migration 0012 applied; `alembic check` → "No new upgrade operations detected" |
| 2.1 | 6.2 | Per-page native-vs-scanned by char density | ✅ | `test_native_pdf_is_not_scanned`, `test_scanned_pdf_is_detected_as_scanned` |
| 2.2 | 6.2 | Mixed document decided per page | ✅ | `test_mixed_document_is_decided_per_page` |
| 2.3 | 6.2 | Encrypted → empty password → else `needs_review` | ✅ | `test_encrypted_pdf_routes_to_review_not_an_exception` · E2E §4 |
| 2.4 | 6.2 | Corrupt → pikepdf repair → else clear error | ✅ | `test_corrupt_pdf_is_repaired_by_pikepdf`, `test_garbage_that_cannot_be_repaired_fails_with_a_clear_error` |
| 3.1 | 6.3 | `get_text("dict")` → words → char-offset ↔ bbox map | ✅ | `test_every_span_indexes_the_text_layer_exactly` · E2E §1 (15 spans, 0 mismatched, through Postgres) |
| 3.2 | 6.3 | Table structure detection | 🟡 | Implemented via PyMuPDF `find_tables()`. **No test, and no real tabular report to test against.** See §4. |
| 3.3 | 6.3 | Reading order, two-column layouts | ✅ | `test_two_column_pages_are_read_a_column_at_a_time` (failed against the first implementation — see D3) |
| 4.1 | 6.4 | OpenCV: greyscale → deskew → denoise → threshold → upscale | ✅ | Runs in the E2E scanned path; `preprocess()` output verified 2339×1653 single-channel |
| 4.2 | 6.4 | PaddleOCR detection + recognition, English | ✅ | E2E §3 — real recognition at **0.997** confidence, `paddleocr==3.7.0` / `paddlepaddle==3.3.1` |
| 4.3 | 6.4 | Per-word confidence; page mean < 0.70 → `needs_review` | ✅ | Confidence recorded end-to-end (0.997); threshold lives in `system_settings`, not in code |
| 4.4 | 6.4 | Rendered page PNG stored for the overlay | ✅ | `test_page_images_are_served_for_the_fallback_view` · E2E §3 (real PNG served over HTTP) |
| 5.1 | 6.5 | pgmq `ingest` consumer | ✅ | Worker log `{"queue": "ingest", "event": "consumer_started"}` · E2E §1 end-to-end through the queue |
| 5.2 | 6.5 | Status written **before and after** each stage | ✅ | `test_page_count_and_status_are_written`; `attempts` incremented by the "before" write |
| 5.3 | 6.5 | 120s per-document timeout, fails cleanly | ✅ | `test_a_document_that_never_finishes_still_reaches_a_human` (real `wait_for`, not a stub) — **and observed firing in production** at exactly 120s. It only became genuinely enforceable once extraction moved to a child process; see D15. |
| 5.4 | 6.5 | DLQ + admin retry button | ✅ | `test_retry_requeues_a_failed_document`, `test_retry_is_refused_while_a_document_is_in_flight` · E2E §5 |
| 5.5 | 6.5 | ★ Fallback to Phase 3 manual entry, page images side-by-side | ✅ | `test_a_scanned_page_gets_an_image_even_when_ocr_is_unavailable` · E2E §4 · and D5 itself is live proof: OCR was entirely broken and **no document was lost** |
| 6.1 | — | Review queue UI | ✅ | `DocumentQueue.test.tsx` — renders a failed document with its reason in plain words, and the empty state |
| 6.2 | — | Highlight overlay UI | ✅ | Rendered in tests; page image fetched **with the bearer token**, since a plain `<img src>` would 401 |
| 6.3 | — | Upload UI | ✅ | Posts multipart with **no hand-set Content-Type** (or the boundary is lost), and admits when no virus scanner is configured |
| 7.1 | — | `.env.example` + runbook document the new settings | ✅ | `.env.example` Phase 6 block; `docs/runbook.md` §9 "An uploaded report is not being read" and §10's pilot checklist |
| 8.1 | 6.6 | Test corpus, 200+ real PDFs | 🔴 | **0 of 200.** Needs the Phase 1.6 corpus. Cannot be satisfied by code. |

---

## 2 · Verification runs

Each row is a command that was actually executed, with its actual result.
**An entry here is the only thing that turns a 🟡 into a ✅.**

| When | What was run | Result |
|---|---|---|
| 2026-09-14 | `docker compose build api` | ✅ built; paddle 3.0.0, paddleocr 3.7.0, cv2 4.10.0, PyMuPDF, pikepdf, libmagic all import |
| 2026-09-14 | `alembic upgrade head` (0012) | ✅ applied |
| 2026-09-14 | `alembic check` | ✅ "No new upgrade operations detected" — models and migrations agree |
| 2026-09-14 | `pytest tests/test_phase_6_extraction.py tests/test_phase_6_storage_intake.py` | ❌ **4 failed** → D1, D2, D3 → ✅ **26 passed** after fixes |
| 2026-09-14 | `pytest tests/test_phase_6_pipeline.py` | ❌ **12 failed** → D4 → ✅ **16 passed** after migration 0013 |
| 2026-09-14 | `pytest tests/test_phase_6_api.py` | ❌ **7 failed** → D6, D7 → ✅ **10 passed** after fixes |
| 2026-09-14 | `pytest tests/test_phase_6_api.py tests/test_phase_6_pipeline.py` | ✅ **26 passed**, no warnings |
| 2026-09-14 | `npx tsc --noEmit` (web) | ✅ clean |
| 2026-09-14 | `docker compose restart worker` | ✅ `{"queue": "ingest", "event": "consumer_started"}` |
| 2026-09-14 | **E2E against the live stack** — real HTTP login, upload, pgmq, worker | **20 of 22 checks passed.** 1 was a script bug (D8), 1 was real: **D5**. |
| 2026-09-14 | `ruff check .` | ❌ 13 errors → ✅ **All checks passed** |
| 2026-09-14 | `black --check .` | ❌ 17 files (all Phase 6) → ✅ clean after formatting |
| 2026-09-14 | `mypy app worker` (strict) | ❌ 9 errors → ✅ **no issues in 117 source files** |
| 2026-09-14 | `pytest` — **whole backend suite** | ❌ 2 earlier-phase regressions (D13, D14) → ✅ **1040 tests, exit 0** |
| 2026-09-14 | `vitest run` — **whole frontend suite** | ✅ **187 tests, 11 files** — Phases 1–5 untouched |
| 2026-09-14 | **E2E, second run** (rebuilt image, pinned paddle, baked models) | ❌ **worker crash-loop** → D10, D11, D12. Document redelivered **16 times**; worker restarted **14 times**. |
| 2026-09-14 | Deliberate revert of D10/D11/D12 fixes, then `pytest` | ✅ all three regression tests went **red**, then green when restored — they detect the defect, not merely the fix |
| 2026-09-14 | Direct OCR reproduction in the worker container | ❌ printed **`Killed`** — the OOM killer, confirming D15 rather than inferring it |
| 2026-09-14 | OCR timing, 2 CPUs vs 6 CPUs, on a real A4 page | **115.4s → 38.7s.** Oversubscription ruled out first (`OMP_NUM_THREADS=2` → 116.3s). |
| 2026-09-14 | **E2E, final run** | ✅ **ALL END-TO-END CHECKS PASSED**, exit 0. Scanned page read by real PaddleOCR at **0.997** confidence, spans exact, fallback and retry both working. |
| 2026-09-14 | `ruff` · `black --check` · `mypy app worker` (final) | ✅ all clean — 185 files, 117 source files |
| 2026-09-14 | `pytest` — whole backend suite (final) | ✅ **1048 tests, exit 0** |
| 2026-09-14 | `vitest run` + `tsc --noEmit` (final) | ✅ **187 tests, 11 files**; typecheck clean |

---

## 3 · Defects found, and their fate

| # | Found by | Defect | Fixed | Regression test |
|---|---|---|---|---|
| **D1** | `pytest` | **Scanned-page threshold too high.** `0.5` chars/1000pt² ≈ 250 characters on A4 — a short real report ("HIV: Non-reactive" under a letterhead) was called scanned and its perfect text layer thrown away for an OCR guess. | ✅ → `0.1` (~50 chars). Errs low on purpose: calling a scanned page native yields no text and routes to review anyway; calling a native page scanned **corrupts good data**. | `test_native_pdf_is_not_scanned` — failed at 0.5, passes at 0.1 |
| **D2** | `pytest` | **Owner-locked PDFs.** The test asserted our `decrypted_with_empty_password` bookkeeping flag rather than the outcome the plan asks for. PyMuPDF never sets `needs_pass` when the *user* password is empty, so the flag is correctly False. | ✅ Test now asserts the outcome — the document opens and its text is readable. Code unchanged; it was already right. | `test_an_owner_locked_pdf_is_read_rather_than_sent_for_review` |
| **D3** | `pytest` | ★ **Two-column pages came out interleaved.** Gutter detection ran on PyMuPDF *blocks*, and a two-column page whose columns share baselines returns one full-width block per row — so no gutter exists to find. Text read `LEFT-00 RIGHT-00 LEFT-01…`; **on a lab report that pairs each analyte with the wrong column's number.** | ✅ Rewrote `native.py` to detect gutters and order reading at **word** level. | `test_two_column_pages_are_read_a_column_at_a_time` — failed against the block-level version (observed `multi_column=False`, interleaved text), passes now |
| **D4** | `pytest` (first DB run) | **Every upload failed.** `audit_log.entity_type`'s CHECK did not list `document`, and intake writes its audit row in the same transaction as the document row — so the violation rolled the upload back. | ✅ Migration `0013_audit_document_entity` + the model list. | All 16 pipeline tests; every one failed before it |
| **D5** | ★ **end-to-end run** | ★ **OCR never worked at all, silently.** Three separate causes, each hidden by the next: (a) PaddleOCR 3.x raises `ValueError` for unknown kwargs, not `TypeError`, so the version fallback never ran and the engine cached itself "unavailable"; (b) paddlepaddle 3.0.0 cannot load paddleocr 3.7's PP-OCRv6 models; (c) oneDNN raises on every page, and PaddleOCR rejects single-channel images with a bare `IndexError`. | ✅ Catch `ValueError` too; pin `paddlepaddle==3.3.1`; `enable_mkldnn=False`; convert to 3-channel BGR. **Verified: 99.99% confidence on a rendered page.** | Probe → real recognition; E2E §3 |
| **D6** | `pytest` | **Test setup, not product.** `documents.uploaded_by` is an FK to `users.id`, and `authenticate_as`'s stub admin exists only in the test process. Phases 1–5 never noticed because none of their endpoints write the acting user into a new row. | ✅ API tests build real user rows. | `_signed_in` helper |
| **D7** | `pytest` | **`error_text=None` meant two things.** "Leave it alone" and "clear it" were the same value, so the retry endpoint's attempt to clear the previous failure's message did nothing — a re-queued document still showed the old error in the review queue. | ✅ Three-valued parameter with an `UNSET` sentinel. | `test_retry_requeues_a_failed_document` — failed before |
| **D8** | end-to-end run | **Script bug, not product.** The E2E script generated the PDF twice and asserted deduplication. PyMuPDF stamps a creation timestamp into every save, so the two files genuinely differ — content addressing was right to call them different documents. | ✅ Script generates once and reuses the bytes. | E2E §2 |
| **D9** | end-to-end run | **OCR models downloaded on first use.** On an air-gapped hospital server — the deployment this product is designed for — that download never succeeds, so OCR would be permanently unavailable on exactly the machines it was built for, and quietly, because the pipeline degrades by design. | ✅ Models baked into the image at build time. | Image builds with models present; worker logs `ocr_engine_ready` with no download |
| **D10** | ★★ **end-to-end run** | ★★ **A poison document crash-looped the worker and took Phases 2 and 4 down with it.** The DLQ check only runs when the handler *raises*. A message that **kills the process** never reaches it — the visibility timeout lapses, pgmq redelivers, the process dies again. Observed: one document redelivered **16 times**, well past `MAX_ATTEMPTS=5`, the worker restarting every ~60s. And the worker process also carries the SLA-timer and notification consumers, so **one unreadable PDF was stopping Phase 2's escalation and Phase 4's messaging every minute.** That is a later phase breaking an earlier one — the thing this project forbids above all else. | ✅ Dead-letter on *arrival* when `read_ct > MAX_ATTEMPTS`, before the handler runs. Whatever killed us last time is not run again. | `test_a_message_that_killed_the_process_is_dlqd_without_rerunning_it` — **verified red without the fix**; plus `test_a_message_at_the_attempt_limit_still_gets_its_last_try` so the guard cannot silently steal an attempt |
| **D11** | end-to-end run | **Rendering ignored page size.** A fixed DPI on an oversized page produced a **15.5-megapixel** bitmap. That was the proximate cause of D10. | ✅ Cap the longest side at 4000px — PaddleOCR's own `max_side_limit`, so every pixel above it was being resized away by the engine anyway. Scale the *zoom* rather than resampling, so the oversized bitmap is never allocated. `points_per_pixel` now derives from the zoom actually used, or spans on a downscaled page would land in the wrong place. | `test_an_enormous_page_is_rendered_within_the_size_cap` (**red without the fix**), `test_a_downscaled_page_still_maps_its_pixels_back_to_points`, `test_a_normal_page_is_not_downscaled` |
| **D12** | end-to-end run | **Non-local-means denoising is unbounded.** Cost grows with pixels × window area; on the 15.5 MP page it ran for minutes and drove the memory limit. | ✅ Above 8.3 MP, degrade to a median blur — removes scanner speckle, costs almost nothing, and *finishes*. The slow path must never be the one that can hang. | `test_denoising_takes_the_cheap_path_on_a_huge_image` (**red without the fix**) |
| **D13** | full suite | **Phase 6 broke a Phase 1 test.** `test_migration_round_trip` downgrades the whole chain; migration 0013's `downgrade()` re-validated a narrower CHECK against existing rows and refused to run once any `document` audit row existed. And `audit_log` is append-only — those rows can *never* be deleted — so the chain became **permanently undowngradable after the first upload**, stranding a hospital on a version it wanted to roll back. | ✅ `NOT VALID`: history survives, new document rows are still refused after a downgrade. | `test_migration_0005_round_trips` — failed before, passes now |
| **D15** | ★★ end-to-end run | ★★ **OCR OOM-killed the whole worker process.** Even after the render cap, PaddleOCR on an 11 MP page exceeded the 3 GB limit and the kernel SIGKILLed the worker — which also runs the SLA-timer and notification consumers. A thread cannot be protected from that: anything that kills the interpreter kills Phase 2's timers with it. Confirmed directly: the reproduction printed `Killed`. | ✅ **Extraction moved into a child process** (`ProcessPoolExecutor`). A dead child raises `BrokenProcessPool` in the parent, which marks the document failed and carries on; the timers never notice. It also makes the 120s timeout *real* — a thread could not be interrupted, a pool can be torn down. Memory raised 3G → 4G as a belt to those braces. | `test_a_child_that_is_killed_does_not_take_the_worker_with_it` — uses a real process pool and `os._exit(1)`, the honest simulation of SIGKILL; also asserts the pool **recovers**, so one bad document does not poison every document after it |
| **D16** | measurement | **A normal scanned page did not fit in the 120-second budget.** One A4 page at 200 DPI took **115.4s** at a 2-CPU quota — so every scanned document would have timed out and gone to a human, making the whole scanned path useless. Thread oversubscription was ruled out first (`OMP_NUM_THREADS=2` made no difference: 116.3s). It is simply CPU-bound. | ✅ Worker CPU limit 2.0 → 6.0. **Measured: 115.4s → 38.7s**, and 45.9s including a cold model load — comfortably inside 120s. NODE A has 8 physical / 12 logical cores, so this leaves room for Postgres, the API and the machine's owner. | The measurement itself, repeated before and after; E2E §3 now reaches `extracted` at 0.997 confidence |
| **D17** | measurement | **The scanned-PDF fixture built a page twice A4.** It sized the new page from the pixmap's *pixel* count, so a 200 DPI render of A4 became a 1190 × 1684 pt page — four times the area a scanner actually produces. Every timing and memory figure taken from it was therefore meaningless, and it is what pushed D11/D15 into the extreme. | ✅ The fixture keeps the source page's size in points, as a scanner does. | `test_scanned_pdf_is_detected_as_scanned` and the timing harness both now work on a real A4 page |
| **D18** | full suite | **A dev login broke a Phase 4 test.** `test_resolution_never_returns_silently` marked *its own* admin absent, but the final fallback looks for **any** administrator — so it only ever passed because no other admin row existed. Seeding a dev login exposed it. In a real hospital, with real admins, the test's premise was never true. | ✅ The test now marks **every** active admin absent, so its setup actually achieves what its docstring claims. | The test itself, strengthened |
| **D14** | full suite | **Phase 6 broke a Phase 2 test.** `test_only_queues_with_a_real_handler_are_consumed` pins the consumed-queue list; adding `ingest` changed it. A legitimate expectation change — `ingest` now has a real handler and a real producer. | ✅ List updated, **and** the test now asserts the underlying property directly: `extract` (Phase 7, no handler) must not be consumed, or its messages would be deleted two seconds after being enqueued. | The test itself, strengthened |

---

## 4 · Deliberately not done, and why

| Item | Decision |
|---|---|
| **Docling** for table pages (6.3) | Replaced with **PyMuPDF `find_tables()`**. Docling pulls PyTorch — ~2.5 GB into an image that must run air-gapped on a CPU-only server — and it is a second extraction engine whose output would have to be *aligned* back to our char-offset ↔ bbox map. That alignment is exactly the fuzzy-matching step `native.py` exists to avoid, and a misaligned span is a citation pointing at the wrong number. Recorded in the phase doc's Deviations table. **Revisit if real reports show the detector missing tables** — that is a 6.6 measurement. |
| **Hindi OCR model** (6.4) | 🔴 Blocked, not skipped. The plan says *"if local labs print bilingual"* — a fact about the local labs that nobody has established. Adding `hi` is a one-line change once someone looks at real paperwork. Same blocker as 6.6. |
| **Watched folder**, run for real | 🟡 Written, and off by default. It cannot be exercised until a real share is mounted into the container, and inventing one would test our own mock. |
| **ClamAV `clean` path** | 🟡 The `skipped` and `error` paths are tested; the `clean` path needs a real clamd, which is not deployed here. The distinction the code is careful about — `skipped` is **never** reported as `clean` — *is* tested. |
| **Table-structure test** | 🟡 `find_tables()` is wired in, but a meaningful test needs a real tabular lab report. A synthetic table would test PyMuPDF, not our handling of what labs actually print. |
| **Tiling large pages for OCR** | Not built. A page above 4.2 MP is downscaled instead, which costs resolution on very large scans. Tiling would preserve it, at real complexity. Revisit if the corpus shows large scans are common and the downscale hurts confidence — i.e. with evidence, not before. |

---

## 5 · Exit Gate 6 — 🔴 **OPEN, and cannot be closed by code**

- [ ] **200 real PDFs** processed, **≥95%** produce usable text
- [ ] Spans map correctly (spot-check the overlay on **20** documents)
- [ ] Failures land in the review queue with page images visible

The third clause is testable now and is tested. **The first two require real
documents and cannot be satisfied by anything in this repository** — the phase
doc's own words: *"do not fake it with synthetic PDFs, they will not surface
the failure modes real labs produce."*

The synthetic PDFs in [`api/tests/_documents.py`](../../api/tests/_documents.py)
are unit-test fixtures for the plumbing. **They are not gate evidence and must
never be counted toward the 200.**

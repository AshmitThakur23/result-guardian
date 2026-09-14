# Phase 6 — Document Ingestion

**Goal:** replace manual typing with file intake. Text out, **with coordinates**.
**Duration:** 2 weeks · **Node:** A only — OCR runs locally, **no GPU required** · **Depends on NODE B?** No

> ⛔ **BLOCKING PREREQUISITE:** the de-identified test corpus from **Phase 1.6**. If it is not ready, this phase cannot start — **do not fake it with synthetic PDFs, they will not surface the failure modes real labs produce.**

---

## 📍 STATUS SUMMARY — Phase 6

> ⚠️ **Do not start this phase until Exit Gate 5 passes.**

**Legend:** ✅ done & verified · 🟡 written, never run · 🔵 in progress · ⬜ not started · 🔴 blocked · 🚫 out of scope
> Tick tasks `- [ ]` → `- [x]` **as you go**, update this table, and log it in [`../../PROGRESS.md`](../../PROGRESS.md). Written is not done.

| § | Node | State | Note |
|---|---|---|---|
| 6.1 Intake channels | A | ✅ done | watched folder is 🟡 — written, off by default, never run |
| 6.2 Type detection | A | ✅ done | threshold corrected 0.5 → 0.1 after it misread a short report |
| 6.3 Native text path | A | ✅ done | Docling replaced by PyMuPDF `find_tables()` — see Deviations |
| 6.4 Scanned path | A | ✅ done | PaddleOCR 3.7.0 / paddlepaddle 3.3.1, models baked into the image |
| 6.5 Pipeline mechanics | A | ✅ done | incl. the mandatory fallback and the 120s timeout |
| 6.6 Test corpus ★ blocks this phase | A | 🔴 blocked | needs the Phase 1.6 corpus — **0 of 200** |
| **Exit Gate 6** | A | 🔴 **OPEN** | needs 200 real PDFs. **Code cannot close this.** |

> **Full build and verification record:**
> [`phase-06-verification-log.md`](phase-06-verification-log.md) — every item,
> the command that proved it, and the nine defects found on the way.
>
> ⚠️ **Exit Gate 6 stays 🔴 OPEN.** The pipeline is built and runs end to end,
> but the gate asks for **200 real PDFs** and the corpus stands at **0**. The
> synthetic PDFs in [`api/tests/_documents.py`](../../api/tests/_documents.py)
> are unit-test fixtures for the plumbing and are **not** gate evidence — the
> prohibition at the top of this file stands.

---

## 6.1 Intake channels

- [x] `POST /api/reports/upload` — multipart, **max 25 MB**, MIME sniffing (**do not trust the extension**) — `app/routers/reports.py`
- [x] 🟡 Watched folder consumer — polls `/data/inbox`, moves to `/data/processing` then `/data/archive/YYYY/MM/DD` — `worker/watched_folder.py`. **Written, never run:** off by default (`RG_WATCHED_FOLDER_ENABLED=false`) until a share is actually mounted.
- [x] 🟡 Virus scan hook (ClamAV container) **before** processing — `app/services/documents/scan.py`. The `skipped` and `error` paths are tested; **the `clean` path has never run against a real clamd**, because no scanner is deployed here.
- [x] Store original file on disk with content-addressed name `sha256.pdf`; **DB holds the path, never the blob** — `app/services/documents/storage.py`
- [x] **`documents`** — id, sha256 (unique — **free deduplication**), original_filename, mime_type, size_bytes, storage_path, page_count, source_channel, uploaded_by, received_at, status (`received|classified|extracting|extracted|failed|needs_review`), error_text
- [x] **`document_pages`** — id, document_id, page_no, width_pt, height_pt, is_scanned, text_layer TEXT, ocr_confidence, image_path
- [x] **`document_spans`** — id, document_id, page_no, char_start, char_end, bbox JSONB `{x0,y0,x1,y1}`, text
  - **Capture spans from day one. Phase 8's span verifier is impossible without them.**
  - ✅ The invariant `text_layer[char_start:char_end] == text` is asserted in memory, after a Postgres round-trip, and end-to-end over HTTP.

## 6.2 Type detection

- [x] PyMuPDF: extract text per page; if `extractable chars / page area < threshold` → treat as scanned
  - ⚠️ The threshold started at 0.5 chars/1000pt² (~250 characters on A4) and was **wrong** — a short real report was called scanned and its perfect text layer thrown away for an OCR guess. Corrected to **0.1** (~50 characters). It errs low on purpose; see `app/services/documents/settings.py`.
- [x] Mixed documents: decide **per page**, not per document
- [x] Encrypted PDF → try empty password → else `needs_review`
- [x] Corrupt PDF → repair attempt via `pikepdf` → else fail with a clear error

## 6.3 Native text path

- [x] PyMuPDF `get_text("dict")` → words with bboxes → build char-offset ↔ bbox map
- [x] ~~Docling~~ **PyMuPDF `find_tables()`** for pages containing detected table structures (**lab reports are tables**) — a deliberate substitution, see [Deviations](#deviations-from-the-build-plan-and-why)
- [x] Preserve reading order; handle two-column layouts
  - ⚠️ First implementation detected columns from PyMuPDF *blocks* and **interleaved the two columns**, because a page whose columns share baselines returns one full-width block per row. Rewritten to work at **word** level.

## 6.4 Scanned path

- [x] OpenCV preprocessing: greyscale → deskew (Hough) → denoise → adaptive threshold → optional upscale for <200 DPI — `app/services/documents/preprocess.py`
- [x] PaddleOCR with detection + recognition, English model — `paddleocr==3.7.0` / `paddlepaddle==3.3.1`, both pinned exactly, models baked into the image so an air-gapped hospital still gets OCR
  - [ ] 🔴 **Hindi model** — "*if local labs print bilingual*". Conditional on a fact about the local labs that no one has established yet. Blocked on the same corpus as 6.6; adding `hi` is a one-line change once someone looks at real paperwork.
- [x] Keep per-word confidence; **page mean confidence < 0.70 → route to `needs_review` instead of guessing** — threshold lives in `system_settings`, not in code
- [x] Store rendered page PNG for the UI highlight overlay

## 6.5 Pipeline mechanics

- [x] pgmq `ingest` queue → worker → status transitions written **before and after** each stage
- [x] Timeout per document (**default 120s**), then fail cleanly — and the test exercises the *real* `asyncio.wait_for`, not a stub
- [x] DLQ + admin retry button — `POST /api/documents/{id}/retry`, plus the button on the document screen
- [x] **Fallback is mandatory:** any failure routes the document to the Phase 3 manual entry form with the page images shown side-by-side. **The workflow never stalls because parsing failed.**
  - ★ Proven the hard way: OCR was **completely broken** for the first end-to-end run (three stacked version defects) and **not one document was lost** — every scanned page was rendered, routed to review, and readable by a human.

## 6.6 Test corpus ★

- [ ] 🔴 Corpus collected during Phase 1.6 is on hand and categorised — **0 of 200+.** Nothing in this repository can satisfy this.

---

## 🔴 EXIT GATE 6 — OPEN

- [ ] 🔴 **200 real PDFs** processed: **≥95%** produce usable text — **0 of 200.** Needs the Phase 1.6 corpus.
- [ ] 🔴 Spans map correctly (spot-check the overlay on **20** documents) — the overlay exists and the span invariant is proven against synthetic PDFs, but **spot-checking 20 real documents needs 20 real documents.**
- [x] ✅ Failures land in the review queue with page images visible — the one clause code can satisfy, and it is tested three ways: unit, end-to-end, and by the accident of OCR being broken for a whole run.

**Why this gate cannot be closed by writing more code.** Two of its three
clauses are measurements over *real hospital paperwork*, and this file's own
blocking prerequisite says why:

> do not fake it with synthetic PDFs, they will not surface the failure modes
> real labs produce

The ≥95% figure is a claim about how our extractor copes with a particular
hospital's fax headers, stamp overlays, carbon-copy scans and bilingual
letterheads. Measuring it against PDFs we generated ourselves would measure
our own fixture generator. **The pipeline is built and runs; the gate stays
open until real documents exist.**

---

## Deviations from the build plan, and why

| Plan says | Built instead | Why |
|---|---|---|
| **Docling** for pages with detected tables (6.3) | **PyMuPDF `find_tables()`** | Docling pulls PyTorch — roughly 2.5 GB into an image that must run on an air-gapped hospital server with no GPU, and it is a second extraction engine whose output would have to be *aligned* back to our char-offset ↔ bbox map. That alignment is precisely the fuzzy-matching step `native.py` exists to avoid, and a misaligned span is a citation pointing at the wrong number. PyMuPDF's own table detector returns cells with bounding boxes in the coordinate system we already use, so the grid is preserved without a second source of truth. **Revisit if real reports show the detector missing tables** — that is a 6.6 measurement, not a judgement to make from synthetic PDFs. |
| PaddleOCR, unpinned | `paddleocr==3.7.0` + `paddlepaddle==3.3.1`, **both exact** | A mismatch between them does not fail at install or at import — it fails on *every page at inference time*, after the engine constructs without complaint. That is exactly how OCR was silently broken for the first end-to-end run. |
| (not specified) | OCR models **downloaded at image build time** | PaddleOCR fetches models on first use. On an air-gapped hospital server — the deployment this whole product is for — that download never succeeds, so OCR would be permanently unavailable on exactly the machines it was built for, and quietly, because the pipeline degrades by design. |
| (not specified) | `documents.processing_started_at` / `processing_ended_at` / `attempts` | 6.5 asks for a timeout and a retry button. Neither is implementable without being able to tell "still working" from "stopped working an hour ago", and a status column alone cannot. |

---

**Cross-ref:** [architecture/02-ingestion-extraction-matching.md](../architecture/02-ingestion-extraction-matching.md) (Step 4)

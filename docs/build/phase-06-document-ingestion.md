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
| 6.1 Intake channels | A | ⬜ not started |  |
| 6.2 Type detection | A | ⬜ not started |  |
| 6.3 Native text path | A | ⬜ not started |  |
| 6.4 Scanned path | A | ⬜ not started |  |
| 6.5 Pipeline mechanics | A | ⬜ not started |  |
| 6.6 Test corpus ★ blocks this phase | A | 🔴 blocked | needs the Phase 1.6 corpus |
| **Exit Gate 6** | A | ⬜ **not started** | |

---

## 6.1 Intake channels

- [ ] `POST /api/reports/upload` — multipart, **max 25 MB**, MIME sniffing (**do not trust the extension**)
- [ ] Watched folder consumer — polls `/data/inbox`, moves to `/data/processing` then `/data/archive/YYYY/MM/DD`
- [ ] Virus scan hook (ClamAV container) **before** processing
- [ ] Store original file on disk with content-addressed name `sha256.pdf`; **DB holds the path, never the blob**
- [ ] **`documents`** — id, sha256 (unique — **free deduplication**), original_filename, mime_type, size_bytes, storage_path, page_count, source_channel, uploaded_by, received_at, status (`received|classified|extracting|extracted|failed|needs_review`), error_text
- [ ] **`document_pages`** — id, document_id, page_no, width_pt, height_pt, is_scanned, text_layer TEXT, ocr_confidence, image_path
- [ ] **`document_spans`** — id, document_id, page_no, char_start, char_end, bbox JSONB `{x0,y0,x1,y1}`, text
  - **Capture spans from day one. Phase 8's span verifier is impossible without them.**

## 6.2 Type detection

- [ ] PyMuPDF: extract text per page; if `extractable chars / page area < threshold` → treat as scanned
- [ ] Mixed documents: decide **per page**, not per document
- [ ] Encrypted PDF → try empty password → else `needs_review`
- [ ] Corrupt PDF → repair attempt via `pikepdf` → else fail with a clear error

## 6.3 Native text path

- [ ] PyMuPDF `get_text("dict")` → words with bboxes → build char-offset ↔ bbox map
- [ ] Docling for pages containing detected table structures (**lab reports are tables**)
- [ ] Preserve reading order; handle two-column layouts

## 6.4 Scanned path

- [ ] OpenCV preprocessing: greyscale → deskew (Hough) → denoise → adaptive threshold → optional upscale for <200 DPI
- [ ] PaddleOCR with detection + recognition, English model; add **Hindi model** if local labs print bilingual
- [ ] Keep per-word confidence; **page mean confidence < 0.70 → route to `needs_review` instead of guessing**
- [ ] Store rendered page PNG for the UI highlight overlay

## 6.5 Pipeline mechanics

- [ ] pgmq `ingest` queue → worker → status transitions written **before and after** each stage
- [ ] Timeout per document (**default 120s**), then fail cleanly
- [ ] DLQ + admin retry button
- [ ] **Fallback is mandatory:** any failure routes the document to the Phase 3 manual entry form with the page images shown side-by-side. **The workflow never stalls because parsing failed.**

## 6.6 Test corpus ★

- [ ] Corpus collected during Phase 1.6 is on hand and categorised

---

## ✅ EXIT GATE 6

- [ ] **200 real PDFs** processed: **≥95%** produce usable text
- [ ] Spans map correctly (spot-check the overlay on **20** documents)
- [ ] Failures land in the review queue with page images visible

---

**Cross-ref:** [architecture/02-ingestion-extraction-matching.md](../architecture/02-ingestion-extraction-matching.md) (Step 4)

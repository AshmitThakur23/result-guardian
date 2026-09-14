"""Document ingestion. Phase 6.

    **Goal:** replace manual typing with file intake. Text out, **with
    coordinates**.

The modules, in the order a document meets them:

| Module | § | What it does |
|---|---|---|
| ``scan`` | 6.1 | ClamAV, before anything is written |
| ``intake`` | 6.1 | size, MIME sniff, dedup, row, queue message |
| ``storage`` | 6.1 | content-addressed files; the DB holds paths only |
| ``detect`` | 6.2 | open / repair / decrypt, and native-vs-scanned per page |
| ``native`` | 6.3 | text layer and spans, built together |
| ``render`` | 6.4 | page PNGs, for OCR and for the fallback form |
| ``preprocess`` | 6.4 | greyscale → deskew → denoise → threshold → upscale |
| ``ocr`` | 6.4 | PaddleOCR behind a boundary that degrades, never crashes |
| ``pipeline`` | 6.5 | the orchestrator, the timeout, and the fallback |
| ``repository`` | — | every database write ingestion makes |
| ``settings`` | — | the three thresholds, from ``system_settings`` |

**The property the whole package is built around** is 6.5's last line:

    **The workflow never stalls because parsing failed.**

Before Phase 6 a clerk typed results into the Phase 3 form and the system
worked. Phase 6 must therefore degrade *to that form* — never to silence, and
never to a crash-looping worker. Every failure path ends with a document a
human can open, with its pages rendered beside the entry fields.
"""

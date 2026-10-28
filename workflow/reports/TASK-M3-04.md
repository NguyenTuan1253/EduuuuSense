# Task Completion Report: TASK-M3-04 — Text Extraction & Chunking Service

## 1. Status
**DONE** (with one disclosed dependency decision)

## 2. Summary of Changes
- ExtractionService: per-page PDF text (pypdf), DOCX paragraphs (python-docx), plain-text passthrough; encrypted -> encrypted_pdf, unparseable -> corrupt_pdf/corrupt_docx, no-text -> empty_document — all map to document processing status failed per the architecture rule "empty/unextractable documents must fail".
- Images (PNG/JPEG) raise ocr_not_configured: real OCR requires a system runtime (e.g., Tesseract) — pending PO decision; they are marked failed meanwhile, never silently skipped.
- ChunkingService: sentence-boundary sliding window producing ~450-token child chunks with ~75-token overlap (DECISION-PROP-02 proposed defaults, pending PO); page provenance preserved for citation metadata (Invariant 6); char-exact budget accounting.
- requirements.txt: added pypdf==5.1.0 and python-docx==1.1.2 — pure-Python libraries required to implement the declared ingestion pipeline; DISCLOSED for PO veto (revert is a two-line change).

## 3. Files Changed
- backend/app/services/extraction_service.py, backend/app/services/chunking_service.py (new), backend/requirements.txt, backend/tests/test_text_extraction.py (new, 8 tests).

## 4. Verification Commands & Real Results
- pytest tests/test_text_extraction.py -q: PASSED — 8 passed (real generated multi-page PDF parsed by pypdf, DOCX built by python-docx, stable failure codes, chunk budget/overlap, page provenance).
- pytest -q: PASSED — 190 passed. ruff: PASSED. mypy: PASSED (64 files).

## 5. Unresolved Risks & Decisions
- PO decisions: (a) keep or reject pypdf/python-docx additions; (b) provision an OCR runtime or leave image documents failing; (c) confirm chunk sizing defaults (450/75).
- Vietnamese diacritics extraction from PDFs depends on embedded fonts (pypdf behavior) — needs the golden-dataset evaluation (M4-08) to quantify.

## 6. Suggested Next Task
- TASK-M3-05: Embedding Generation & pgvector Storage (US 2.10).

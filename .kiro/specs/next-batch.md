# Spec: Next Batch — v4.1 Close-Out + v4.5 Foundation

**Date:** 2026-05-05  
**Status:** Planning

---

## Context

The roadmap's v4.1 ingestion-fidelity batch is nearly done. P0.0–P0.4 and P0.6 are shipped. P0.5 (LLM role inference) is implemented but needs an end-to-end audit pass. P0.7 (regression suite) has fixtures but is missing snapshot assertions. P0.8 (PDF table extraction) is the only fully open item.

v4.4.1 (chat orchestration) and v4.4.2 (provider capability clarity) are both shipped. The primary product surface — in-app streaming chat — is working.

The next logical batch is:

1. **Close out v4.1** — P0.5 audit, P0.7 snapshot assertions, P0.8 PDF tables.
2. **Begin v4.5** — audio → structured notes (the transcript layer is already live; the LLM extraction pass is the missing piece).

These are ordered by dependency: P0.5 and P0.7 are low-risk cleanup; P0.8 is self-contained; v4.5 audio notes build on the already-shipped transcription path.

---

## Requirements

### R1 — P0.5 Audit: verify LLM role inference end-to-end

The `infer_roles_with_llm` function and `role_source` provenance tagging are implemented. What's unverified:

**R1.1** — `collection_id` must be threaded from `HoldingsStore.create_table()` through to `infer_roles_with_llm()` so per-collection Presidio profiles apply to sample redaction. Currently `collection_id=None` is hardcoded at the call site in `holdings_store.py`.

**R1.2** — Every MCP tool other than `compute_portfolio_metric` must work correctly when `financial_roles` is empty. Specifically: `query_table`, `get_table_rows`, `aggregate_table`, `search_collection`, `get_document_context` must be role-agnostic. Any silent dependency on a role is a bug.

**R1.3** — When `compute_portfolio_metric` is called on a table with no detected roles, it must return a structured error (`"no_role_detected"`) rather than an empty result or a wrong number. The calling LLM should be directed to use `query_table` instead.

**R1.4** — Add a test that ingests a file with completely unknown column headers (no vendor profile, no heuristic match), verifies: (a) `query_table` returns correct rows with raw headers, (b) `compute_portfolio_metric` returns the structured error.

**R1.5** — Confirm `get_table_schema` MCP response includes `role_source` per column and a top-level `financial_role_sources` map. Add a test that asserts these fields are present and correctly populated for profile/heuristic/llm/null cases.

---

### R2 — P0.7 Snapshot assertions for regression suite

Five fixtures exist: `pershing_unrealized_gl.csv`, `schwab_unrealized_gl.csv`, `fidelity_positions.csv`, `vanguard_holdings.csv`, `netx360_holdings_by_investor.csv`. The existing `test_ingest.py` runs the ingestion path but lacks snapshot-based assertions.

**R2.1** — For each fixture, add snapshot assertions covering:
- Detected header row index
- Column count after header detection
- Column names (the actual detected headers, not `Unnamed: N`)
- Role map: which columns got which roles, and via which source (`profile`/`heuristic`)
- Type map: which columns are `real` vs `text` vs `date`
- At least one canonical aggregate from `compute_portfolio_metric` (e.g. `total_market_value` for holdings files)

**R2.2** — Snapshots are stored as JSON files in `tests/fixtures/snapshots/`. A helper function loads the expected snapshot and diffs against the actual result, printing a clear diff on failure.

**R2.3** — CI runs the full ingestion path against every fixture. Any drift in header detection, role mapping, or aggregate values requires explicit snapshot update (not silent pass).

---

### R3 — P0.8 PDF table extraction

**R3.1** — Detect tables in PDFs using pdfplumber's `page.extract_tables()`. For each page, attempt table extraction before falling back to `page.extract_text()`. If a page yields one or more tables, route them through the tabular pipeline; if it yields only text, keep the existing text path.

**R3.2** — Extracted PDF tables flow through the same pipeline as CSV/XLSX: header detection (`_detect_header_row`), numeric coercion (`_parse_generic_number`), role mapping, and vendor profile detection. The output shape is identical to `extract_tabular_sheets()` — a list of sheet dicts with `columns`, `rows`, `row_texts`, `document_metadata`.

**R3.3** — A PDF that contains both tables and prose text produces two outputs: the tabular sheets (routed to `HoldingsStore`) and the prose text (routed to the normal chunking/indexing pipeline). These are not mutually exclusive.

**R3.4** — Vendor profiles apply to PDF tables the same way they apply to CSV. A Schwab PDF statement with the same column layout as a Schwab CSV should land identically.

**R3.5** — When pdfplumber table extraction yields no tables on a page (returns `None` or empty list), fall through to the existing text extraction path. No regression on text-only PDFs.

**R3.6** — Add a test fixture: a minimal PDF containing a table (can be generated programmatically with `reportlab` or `fpdf2` in the test setup). Assert that the table is extracted, header-detected, and role-mapped correctly.

**R3.7** — Docling is already available as an OCR fallback. If pdfplumber table extraction fails or returns low-confidence results (fewer than 2 columns, fewer than 2 rows), attempt Docling's table extraction as a secondary pass. Docling is not required — if unavailable, pdfplumber-only is acceptable.

---

### R4 — v4.5 Audio → structured notes

The transcription path is live: audio files are transcribed via faster-whisper, chunked into ~4-minute pages, and indexed as documents. The missing piece is the LLM extraction pass that turns transcript text into typed, queryable structure.

**R4.1** — New function `extract_meeting_notes(transcript_text: str, collection_id: str, provider: AIProvider) -> MeetingNotes` in a new module `services/meeting_notes.py`. Takes the full transcript text, calls the configured LLM with a structured extraction prompt, returns a `MeetingNotes` dataclass.

**R4.2** — `MeetingNotes` dataclass fields:
```python
@dataclass
class MeetingNotes:
    client_concerns: list[str]          # verbatim or paraphrased concerns raised
    decisions: list[str]                # decisions made during the meeting
    action_items: list[ActionItem]      # see R4.3
    follow_up_questions: list[str]      # questions to research before next meeting
    sentiment_notes: str | None         # brief narrative on client mood/tone
    raw_transcript_doc_id: str | None   # back-reference to the source transcript
```

**R4.3** — `ActionItem` dataclass:
```python
@dataclass
class ActionItem:
    description: str
    assignee: str | None    # "advisor", "client", or a name
    due_date: str | None    # ISO date string or natural language ("next meeting")
    status: str             # "open" | "closed"
```

**R4.4** — The extraction prompt instructs the LLM to output a JSON object matching the `MeetingNotes` schema. The prompt is conservative: if a field cannot be determined from the transcript, it should be an empty list or null — never fabricated. The prompt explicitly says "do not invent action items or decisions that are not clearly stated in the transcript."

**R4.5** — `MeetingNotes` is stored as a row in a new `meeting_notes` table in the collection's `metadata.db`. Schema:
```sql
CREATE TABLE IF NOT EXISTS meeting_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id     TEXT NOT NULL,          -- the transcript document
    collection_id   TEXT NOT NULL,
    extracted_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    client_concerns TEXT,                   -- JSON array
    decisions       TEXT,                   -- JSON array
    action_items    TEXT,                   -- JSON array of ActionItem dicts
    follow_up_questions TEXT,               -- JSON array
    sentiment_notes TEXT,
    raw_json        TEXT NOT NULL           -- full LLM response for audit
)
```

**R4.6** — Extraction is triggered automatically after a transcript document is indexed, if a configured AI provider is available. It runs as a background task (non-blocking). If extraction fails, the transcript is still indexed and searchable — extraction failure is logged but does not block ingest.

**R4.7** — New MCP tool `get_meeting_notes(collection_id, document_id=None, date_range=None)` returns structured notes for one or more meetings. When `document_id` is provided, returns notes for that specific transcript. When `date_range` is provided, returns all meetings in that range. Returns the `MeetingNotes` structure plus the source document metadata.

**R4.8** — New MCP tool `list_action_items(collection_id, status="open", assignee=None)` returns all action items across all meetings in a collection, filterable by status and assignee. This is the primitive that makes "show me every open action item across all my Henderson meetings" answerable deterministically.

**R4.9** — PII redaction applies to meeting notes before they are returned by any MCP tool. The extraction itself runs locally against the transcript (which is already stored locally), so no PII leaves the machine during extraction. The MCP tool responses go through the existing redaction middleware.

**R4.10** — Add a test that: (a) provides a synthetic transcript with clear action items and decisions, (b) runs `extract_meeting_notes`, (c) asserts the returned `MeetingNotes` contains the expected action items and decisions, (d) asserts the `meeting_notes` table row is created correctly.

---

## Design notes

### P0.5 audit scope

The audit is narrow: thread `collection_id`, verify role-agnostic tools, add the no-role error path test. The LLM inference logic itself (`infer_roles_with_llm`, `_redact_samples`, `_parse_response`) is already tested in `test_llm_role_inference.py`. Don't re-test what's already covered.

### P0.8 implementation approach

pdfplumber is already imported in `document_extractor.py`. The `_extract_pdf_with_pdfplumber` method calls `page.extract_text()` — extend it to also call `page.extract_tables()` and collect any non-empty results. The table extraction path produces a list of `list[list[str | None]]` (rows × cells), which maps directly to the same shape that `_extract_csv` produces after pandas parsing. Feed it through `_detect_header_row` and the existing tabular pipeline.

The key design decision: `extract_text()` returns an `ExtractionResult` (text only). PDF tables need to also surface as tabular sheets. The cleanest approach is to add an optional `tabular_sheets` attribute to `ExtractionResult` that the upload service checks — same pattern as how CSV/XLSX already routes through `extract_tabular_sheets()`. Alternatively, detect `.pdf` in `upload_service.py` and call a new `extract_pdf_tables()` method alongside `extract_text()`. The latter is less invasive.

### v4.5 scope boundary

This spec covers the extraction pass and the two MCP tools (`get_meeting_notes`, `list_action_items`). The `prep_for_meeting` composite tool and the `meetings` first-class doctype are v4.5 follow-ons — they depend on this foundation but are not in scope here. The frontend UI for viewing notes is also deferred.

### Ordering

1. R1 (P0.5 audit) — 1–2 hours, low risk, closes a known gap
2. R2 (P0.7 snapshots) — 2–3 hours, mechanical, high value for regression safety
3. R3 (P0.8 PDF tables) — 4–6 hours, self-contained, uses existing pdfplumber import
4. R4 (v4.5 audio notes) — 4–6 hours, new module, builds on shipped transcription

---

## Tasks

- [ ] **T1.1** Thread `collection_id` from `HoldingsStore.create_table()` to `infer_roles_with_llm()` call site
- [ ] **T1.2** Audit `query_table`, `get_table_rows`, `aggregate_table`, `search_collection`, `get_document_context` for silent role dependencies; fix any found
- [ ] **T1.3** Add structured `"no_role_detected"` error path to `compute_portfolio_metric` when required roles are absent
- [ ] **T1.4** Add test: ingest unknown-header file → `query_table` returns raw headers → `compute_portfolio_metric` returns structured error
- [ ] **T1.5** Add test: `get_table_schema` response includes `role_source` per column and `financial_role_sources` map for all four source values
- [ ] **T2.1** Add snapshot JSON files for all 5 fixtures (header row, columns, roles, types, one aggregate)
- [ ] **T2.2** Add snapshot diff helper and wire into `test_ingest.py`
- [ ] **T2.3** Verify CI runs all fixture assertions (no new CI config needed if pytest already runs `test_ingest.py`)
- [ ] **T3.1** Extend `_extract_pdf_with_pdfplumber` to call `page.extract_tables()` and collect non-empty tables
- [ ] **T3.2** Add `extract_pdf_tables(pdf_path) -> list[dict]` method to `DocumentExtractor` returning tabular sheet dicts
- [ ] **T3.3** Wire `extract_pdf_tables` into `upload_service.py` alongside the existing `extract_text` call for `.pdf` files
- [ ] **T3.4** Apply header detection, numeric coercion, and vendor profile detection to extracted PDF tables
- [ ] **T3.5** Add Docling secondary pass when pdfplumber yields low-confidence tables (optional, gated on `DOCLING_AVAILABLE`)
- [ ] **T3.6** Add test fixture (programmatically generated PDF with a table) and assert extraction + role mapping
- [ ] **T4.1** Create `services/meeting_notes.py` with `MeetingNotes`, `ActionItem` dataclasses and `extract_meeting_notes()` function
- [ ] **T4.2** Add `meeting_notes` table to collection `metadata.db` schema (migration in `HoldingsStore._init_db` or equivalent)
- [ ] **T4.3** Wire extraction trigger in `upload_service.py` after transcript indexing completes (background task, non-blocking)
- [ ] **T4.4** Implement `get_meeting_notes` MCP tool in `mcp_server.py`
- [ ] **T4.5** Implement `list_action_items` MCP tool in `mcp_server.py`
- [ ] **T4.6** Verify redaction middleware covers `get_meeting_notes` and `list_action_items` responses
- [ ] **T4.7** Add test: synthetic transcript → `extract_meeting_notes` → assert action items + decisions → assert DB row created

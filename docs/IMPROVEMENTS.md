# Performance & UX Improvement Track

Working list for the performance / portability / sidebar-UX effort (2026-07-02).
Complements [ROADMAP.md](ROADMAP.md) (product features); this file tracks
engineering-quality work. Check items off as they land.

## Shipped

- [x] **Batch SQLite lookups in the search path.** `get_chunk_by_index` was a
  `LIMIT 1 OFFSET ?` table scan per FAISS hit (O(n²) with filters active).
  Added `get_chunks_by_indices` / `get_chunks_by_chunk_ids` /
  `get_positions_for_chunk_ids` / `get_documents_info` batch methods;
  `VectorStore.search` and `search_hybrid` now filter FAISS hits by position
  first and fetch metadata for final results in 1–2 queries. Also removed two
  load-all-chunks-into-RAM paths (`_get_chunk_by_id`,
  `_bm25_to_search_results`).
- [x] **Stop blocking the event loop on search.** FastMCP calls sync tools
  directly on the loop, and `/search` ran embedding + FAISS + synthesis
  inline — one search froze the UI and every MCP sidecar. `search_collection`,
  `search_all_collections`, `find_in_documents`, `/search`, and `/api/embed`
  now offload CPU work via `asyncio.to_thread`.
- [x] **Stop inlining PDF-extracted tables into every response.** Tables
  extracted from PDF/DOCX pages were inlined as "authoritative CSV data" into
  every search/chat prompt (72 KB of a 74 KB MCP response) and, via
  `skip_filenames`, suppressed those documents' text chunks from results.
  Only true tabular sources (csv/tsv/xlsx/xls) are inline candidates now.
- [x] **Fix silent search-history failure.** History writer referenced fields
  that don't exist on `SearchResult` (`chunk_index`/`text`/`similarity`);
  the AttributeError was swallowed, so no search was ever recorded.
- [x] **Code-split the frontend.** All tabs were statically imported, so
  gpt-tokenizer's ~2 MB token tables shipped on first paint. Non-default tabs
  are now `defineAsyncComponent`; main bundle 2,516 kB → 352 kB (gzip ~120 kB).

- [x] **Chat endpoints (and other sync-in-async handlers) off the event
  loop.** `/api/chat`, `/api/ask`, uploads, local indexing, OCR playgrounds,
  key validation, document/collection deletes, folder scan, and the tkinter
  file pickers (which froze the whole server while the dialog was open) are
  now plain `def` handlers running in FastAPI's threadpool. `/api/chat/stream`
  wraps every provider round-trip, search, and tool execution in
  `asyncio.to_thread` between SSE events. The three async MCP search tools
  were split into `*_sync` implementations + thin async wrappers so in-process
  callers (chat tool executor) get results, not coroutines. Verified live:
  worst-case `/health` latency during a streaming tool-calling chat is ~0.25s.

## Remaining — ordered

- [x] **Trim install weight.** Core `requirements.txt` no longer pulls
  docling / faster-whisper / pytesseract / psycopg2 (and the fintech-only
  `yfinance` leak is gone). Optional sets live in `requirements-ocr.txt`,
  `requirements-audio.txt`, `requirements-postgres.txt`; all were already
  lazily imported with graceful fallbacks. Docker images and the desktop
  bundle still install everything, so their behavior is unchanged.
  `/api/capabilities` now reports `ocr_available` / `audio_available` /
  `postgres_available` for adaptive UI.
- [x] **FTS5-backed BM25.** `bm25_service.py` rewritten on SQLite FTS5 with
  its native `bm25()` ranking behind the identical `BM25Index` interface;
  same tokenizer semantics (lowercase, ≥2-char alphanumerics, stopwords).
  Legacy term-frequency databases migrate in place by reconstructing
  bag-of-words docs (BM25 is order-independent) and are VACUUMed after.
  Real-data result: default collection 23.6 MB → 2.9 MB, migration 2.2 s,
  keyword search ~19 ms.

- [x] **Id-mapped FAISS index.** `VectorStore` now uses `IndexIDMap2` over
  `IndexFlatIP`, keyed by the chunk's SQLite row id: deletes are in-place
  `remove_ids` (no rebuild), search hits are primary-key lookups (no
  ROW_NUMBER position mapping), and the `embeddings.npy` shadow copy is gone
  (reconstruct-by-id covers it). Legacy indexes migrate on first open; when
  index/metadata sizes disagree the migration flags the drift and
  `DocumentIndexer.rebuild_vector_index()` re-embeds every chunk from the
  text already stored in SQLite (no source files needed).
  **Found in the process:** the live default collection had 14,363 vectors
  for 5,853 chunks — the old positional mapping was silently misattributing
  semantic results. After rebuild: 6/6 self-match at cos=1.0, and live
  queries return visibly better-ranked results.

> **⚠ Discovered along the way:** `.env` has `DATA_DIR=/app/data` (a Docker
> leftover). On Windows that resolves drive-relative to `D:\app\data`, which
> is where the live collections actually are — the repo's `data/` directory
> is a stale copy. Worth fixing deliberately (point DATA_DIR at a real
> location and consolidate) since it will bite the moment the repo moves to
> another drive or machine.

- [x] **Compact / companion mode (≤767 px).** The sources and analysis panels
  previously sat in-flow at 320 px each and defaulted open — pinned at 400 px
  the answer surface got zero width. Now a `matchMedia` breakpoint switches
  both panels to overlays (absolute, capped at 85 % of the window, shadow +
  backdrop, one at a time, tap-outside closes) and they start closed in
  compact mode, while desktop behavior is untouched. Verified with Playwright
  at 400 px (main keeps full width, no horizontal scroll, overlay opens and
  closes) and 1200 px (unchanged: both panels in-flow at 320 px).
  Still open for later: a PWA manifest so the web build installs as a
  standalone window.
- [x] **No more full-corpus scans in `find_in_documents`.** Literal ASCII
  patterns are prefiltered in SQL (`INSTR`), so only candidate chunks leave
  the database (a live query scanned 20 rows instead of 5,853); regex and
  non-ASCII patterns stream through a fetchmany cursor without materializing
  the corpus. `get_all_chunks_ordered` remains only in the intentional
  full-read paths (index rebuild, unused `index_repair.py`).

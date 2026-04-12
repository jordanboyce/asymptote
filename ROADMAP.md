# Asymptote Roadmap

Forward-looking work for Asymptote. Items that have already shipped are not listed here — check the git log for history.

---

## Strategic frame

Asymptote's product surface is the **MCP server** ([services/mcp_server.py](services/mcp_server.py)), not the web chat. The Vue frontend stays as an admin/config UI; the primary way users query their data is by pointing Claude Desktop / Cursor / ChatGPT / OpenClaw at the MCP endpoint. Every roadmap item is evaluated against "does this make Asymptote a better tool for a calling agent?"

**Asymptote's job is to be the trustworthy data layer for an LLM agent.** The intelligence layer lives upstream in the host LLM. Asymptote owns:

1. **Ingest arbitrary tabular data** from arbitrary tools (brokerages, banks, CRMs, planning software, internal systems) and make every file faithfully agent-queryable, regardless of vendor or column naming convention.
2. **Map vendor-specific schemas to canonical semantic roles** so the same agent question works against any source.
3. **Expose analytical and enrichment primitives** as MCP tools that compose against that semantic layer.
4. **Never be silently wrong.** A financial-adjacent tool that returns confidently incorrect numbers is worse than no tool. Aggregations, type coercions, and unit conversions must surface uncertainty rather than collapse it.

Financial advisors are the first wedge, but the architecture is general — any tabular export from any tool should land cleanly.

---

## Why this batch is reordered (read first if picking this up cold)

Three advisor sessions against a real Schwab unrealized-gain/loss CSV surfaced bugs that make the previous batch order wrong:

- **CSV header row was not detected.** The file had 8 preamble rows (`Quote Type`, `Cash Included`, `As Of`, blanks); real headers were on row 9. [services/document_extractor.py:550](services/document_extractor.py#L550) calls `pd.read_csv(csv_path)` with no header sniffing, so all 20 columns landed as `Unrealized_Gain_Loss`, `Unnamed__1`, …, `Unnamed__19`. Every downstream metric in `compute_portfolio_metric` is dead for that file because role detection has nothing to match against.
- **Numeric values came in as strings with commas.** `"1,591.20"` stored as TEXT, then `CAST(... AS DOUBLE)` silently coerces to a small integer. The agent's lot-rollup query reported market values like `1`, `2`, `14` for positions worth thousands of dollars — and showed those numbers to the advisor with no warning.
- **`REPLACE` was in `_FORBIDDEN_KEYWORDS`** (a SQLite string function) so the agent couldn't strip commas as a workaround. **Fixed** in [services/structured_store.py:224](services/structured_store.py#L224).
- **No lot rollup primitive.** PKST appeared 16 times because every tax lot is a row. The agent had to GROUP BY by hand and produced wrong sums (see above).
- **Questions like "what's in a downtrend?" and "growth vs value?"** can't be answered correctly from the file alone — they require external data feeds Asymptote doesn't expose. The agent fell back to "loss vs cost basis" and "guess from ticker name." Both gave the right shape of answer but neither was actually right.

Conclusion: ingestion fidelity and enrichment feeds are the only thing that matters until the demo above can be re-run and produce correct numbers without manual workaround SQL. Meeting capture, client profiles, and analytics are still on the roadmap, but they sit on top of this foundation.

---

## v4.1 — Ingestion fidelity & arbitrary-sheet handling (P0, urgent)

Quality bar: **any tabular export from any tool should land as a clean, typed, role-mapped table without manual cleanup.** Nothing else ships until this is solid.

### P0.1 — Smart header detection

**Problem:** Brokerage/bank/CRM exports almost always have N preamble rows before the actual header. Current importer assumes row 1 is the header.

**Implementation:**
- In [services/document_extractor.py:550](services/document_extractor.py#L550) (and the two other `pd.read_csv` call sites at lines ~613 and ~721), wrap the read in a header-sniffing pass.
- Heuristic: read the first ~30 rows raw. Find the first row where (a) most cells are non-empty, (b) cells are short strings without numeric/currency formatting, (c) the row immediately below has predominantly numeric/date values. Promote that row to header, drop the rows above as preamble metadata.
- Preserve the dropped preamble as `document_metadata` (`as_of_date`, `currency`, `view_type`, etc.) — useful context for the agent.
- Fall back to `pd.read_csv` defaults if no candidate row is found.
- Same logic applies to XLSX sheets.

### P0.2 — Numeric coercion for currency / accounting strings

**Problem:** `"1,591.20"`, `"$1,591.20"`, `"(123.45)"`, `"1.5K"` all currently land as TEXT. Downstream `CAST(... AS DOUBLE)` silently coerces to wrong values.

**Implementation:**
- During structured ingest in [services/structured_store.py](services/structured_store.py), after header detection, for each column attempt a numeric coercion pass: strip currency symbols, thousands separators, accounting-negative parens, common suffixes (`K`, `M`, `B`). If ≥80% of non-null values coerce cleanly, store the column as REAL not TEXT.
- Record the original raw string in a sibling `_raw` column (or in the column's metadata) so the agent can still see the source if needed.
- Apply the same pass to date-like columns (`Trade Date`, `Settle Date`, `Maturity`) → ISO date strings or DATE.

### P0.3 — Lot / row rollup as a first-class concept

**Problem:** Every brokerage file represents a single position as N rows (one per tax lot). Lots are an implementation detail; no human or agent should care about them.

**Implementation:**
- When a `Symbol` / `CUSIP` / `Security ID` role is detected, automatically expose a logical view alongside the raw table: `<table_name>__by_symbol` (or `positions_by_symbol`) that aggregates market value, cost basis, quantity, gain/loss, and weighted-average unit cost.
- Add a `group_by_symbol: bool = True` parameter to `compute_portfolio_metric` that rolls up lots before computing.
- The raw lot-level table stays available for tax/cost-basis questions that genuinely need it.

### P0.4 — Known-vendor schema profiles

**Problem:** Schwab, Fidelity, Vanguard, Pershing, Raymond James, etc. each export the same logical concepts under different column names and layouts. Heuristic role detection works on common cases but misses the long tail.

**Implementation:**
- New `services/ingest_profiles/` directory containing per-vendor YAML/JSON profiles. Each profile declares: file signature (filename glob, presence of telltale strings, distinctive column set), preamble row count, header row offset, column → semantic role map, type overrides, doc-level metadata extractors.
- At ingest time, run profile detection first. If a profile matches, apply it deterministically. Otherwise fall back to P0.1/P0.2/role-from-name heuristics.
- Ship initial profiles for Schwab (Holdings, Unrealized G/L, Realized G/L, Transactions), Fidelity (Positions, History), and Vanguard (Holdings, Activity). These three cover ~70%+ of advisor file flow.
- Profiles are plain data files — easy for users (or future you) to add new vendors without code changes.

### P0.5 — LLM-assisted column role inference (long tail)

**Problem:** For a never-seen-before export from an unknown tool, neither profiles nor name heuristics fire and `financial_roles` stays empty.

**Implementation:**
- After profile detection and name heuristics, if ≥50% of columns are still unmapped, optionally run a small-LLM pass that takes (column names + 3 sample values per column + the existing role taxonomy) and proposes role assignments with confidence scores.
- Gated behind a setting (`enable_llm_schema_inference: bool`) — costs money, opt-in per-collection or per-upload.
- The output is a *suggestion*, surfaced to the user in the upload UI: "Asymptote thinks `Holdings_USD` is `market_value`, `Sec` is `symbol`. Accept / edit / reject." Once accepted, the mapping is saved and reused for future files matching the same signature.
- Effectively a profile-bootstrapping mechanism — every long-tail file an LLM resolves becomes a candidate profile for next time.

### P0.6 — Numeric sanity guards on aggregates

**Problem:** A `compute_portfolio_metric` or aggregated SQL query that returns nonsensical values (market values < $10 next to gain/loss > $100, weights summing to 12%, NULL leakage in critical columns) should never be returned silently.

**Implementation:**
- After computing any aggregate, run a small set of sanity checks: weights sum to ~100%, market values within plausible orders of magnitude given cost basis, no NULLs in canonical roles when raw data has values, types match expected.
- On failure, attach `warnings: ["aggregation_likely_lost_precision: column X looks numeric but is stored as TEXT"]` to the response. Don't suppress the result, but never ship it without the warning.
- Same guard fires inside the SQL execution path in [services/structured_store.py](services/structured_store.py) — if a query SUMs a TEXT column that contains digit+comma values, warn.

### P0.7 — Regression suite of real exports

**Problem:** Without test fixtures of real broker/bank exports, every fix to the ingestion path risks breaking another vendor's format.

**Implementation:**
- Create `tests/fixtures/ingest/` with at least one anonymized export per supported vendor (Schwab, Fidelity, Vanguard initially). Real-shaped data, fake names/account numbers.
- For each fixture, snapshot the expected: detected header row, column count, role map, type map, and a few canonical aggregates from `compute_portfolio_metric`.
- CI runs the full ingestion path against every fixture and asserts the snapshot. Any drift requires explicit acceptance.
- This is the only thing keeping the "never silently wrong" bar honest over time.

### P0.8 — PDF table extraction (promoted from old v4.3)

**Problem:** Many custodians ship statements as PDF, not CSV. Today there's no path from a PDF statement to `structured_store`.

**Implementation:**
- Detect tables in PDFs (candidate libraries: `camelot`, `tabula-py`, or an LLM vision pass) and route them through the same header-detection / numeric-coercion / role-mapping pipeline as CSV.
- Same vendor profiles apply — a Schwab PDF statement should land identically to a Schwab CSV.
- Charts/figure extraction stays out of scope for now.

### Status of items already in the previous v4.1 batch

The collection-guide / `find_in_documents` / `rows_jsonl` / `identifier` disambiguation / `suggested_next_tools` items are still valid and pair naturally with the new ingestion work, but they move to **v4.3 (MCP surface polish)** below. None of them matter if the underlying data is wrong.

**Already shipped this session:** `REPLACE` removed from `_FORBIDDEN_KEYWORDS` ([services/structured_store.py:224](services/structured_store.py#L224)) so agents can use SQLite string functions to clean numeric strings as a workaround until P0.2 lands.

---

## v4.2 — Enrichment data feeds as MCP tools

Most advisor questions ("what's in a downtrend?", "growth vs value?", "any CEO changes?") fundamentally need data Asymptote doesn't have. Each missing feed becomes a small, contained MCP tool.

### `get_price_history(symbol, start, end, interval='1d')`

- Backed by `yfinance` initially (free, no auth). Swappable to Polygon / Tiingo / Alpha Vantage behind a backend setting once a paying user needs intraday or longer history.
- Returns a small JSON time series. Caches by `(symbol, start, end, interval)` in the existing app DB with a TTL.
- Unblocks: "what's in a downtrend?", momentum screens, drawdown analysis, "show me the chart" questions.

### `get_security_classification(symbol)`

- Returns sector, industry, market cap bucket, country, asset class, and (where available) Morningstar-style box. Backed by `yfinance` `info` initially; can layer a static reference file for higher quality.
- Unblocks: "growth vs value?", sector breakdowns that don't depend on the source file having a sector column, concentration analysis.

### `get_corporate_events(symbol, since, types=['8-K', 'dividend', 'split', 'merger'])`

- Pulls SEC EDGAR (free, no key) for filings; pulls dividend/split/earnings calendars from `yfinance`.
- Unblocks: CEO changes, M&A exposure, dividend cut detection. The CEO-changes question that previously required a 42-ticker web search subagent becomes a single tool call.

### `enrich_holdings(collection_id)` — composite convenience tool

- Walks the holdings table for a collection, calls the three tools above for every distinct symbol, returns a joined view.
- This is the killer because it makes every existing portfolio file *vastly* more answerable without changing how files come in.
- Caches aggressively — re-running the same collection after an hour should be near-instant.

### Design notes

- All four tools are read-only, free-tier-backed by default, and contained. No tool requires a paid API key to ship the first version.
- Each tool's docstring explicitly tells the calling agent when to use it (e.g. `get_price_history`: "use when the user asks about trends, momentum, drawdowns, or any time-series question that requires data beyond the current snapshot in the collection").
- Costs (when paid feeds are wired up) go in a per-collection budget setting so a runaway agent can't blow through an API quota.

---

## v4.3 — MCP surface polish (the previous v4.1 batch, deferred)

These items are still right; they just sit behind the ingestion-fidelity work because they don't help if the data is wrong.

### Per-collection guide memory

User-editable markdown blob per collection surfacing currency assumptions, entity aliases ("Jane" = Jane Smith), date conventions, plain-language column meanings. New column on `collections` (or dedicated `collection_guides` table). Surfaced in full inside `get_collection_info()` and as a ≤500-char summary inside every `search_collection` response. Frontend: markdown editor in the existing collection settings panel. Default template seeded on creation.

**Why it pays off:** agents using Claude Desktop lose context between sessions. A guide that travels with every tool response is the cheapest way to give Asymptote durable memory.

**Cross-link with v4.5:** the client profile object below is the structured cousin of this. The guide is freeform markdown ("how to think about this collection"); the profile is typed fields ("risk_tolerance: moderate"). They coexist — the guide is for narrative, the profile is for primitives.

### `find_in_documents(pattern, literal=True, collection_id=None, max_results=20)`

New MCP tool backed by [services/bm25_service.py](services/bm25_service.py). Returns filename, page, chunk_id, tight excerpt with match highlighted. Docstring positions it as "use for exact strings, ticker symbols, identifiers, quoted phrases" vs `search_collection` for concepts.

### `rows_jsonl` → structured `rows` output

For tables under (say) 200 rows, emit `rows: list[dict]` or `rows: list[list] + columns: list[str]` instead of the current JSONL string at [services/mcp_server.py:489](services/mcp_server.py#L489). Keep `rows_jsonl` as a fallback only when the row count would bloat the response.

### Disambiguate the overloaded `identifier` parameter

`get_table_schema`, `get_structured_table_rows`, and `compute_portfolio_metric` all accept `identifier` as `table_name | filename | document_id`. Add a clarifying enum `identifier_type: Literal["table_name", "filename", "document_id"] | None = None` (None = auto-detect, current behavior). Non-breaking. Agents that want precision get it.

### `suggested_next_tools` hints in responses

When `search_collection` detects numeric intent (`"total"`, `"average"`, `"how many"`), include `suggested_next: ["list_structured_tables"]` or similar in the response. Pure bonus signal — hosts that ignore it still work.

---

## v4.4 — Meeting capture wedge

The first feature that turns Asymptote from "data layer" into "advisor workflow tool." Built on top of v4.1 + v4.2 — meeting prep is only useful if the portfolio drift it surfaces is correct.

### Audio → transcript → structured notes

- [services/audio_transcriber.py](services/audio_transcriber.py) is already started. Wire it into the upload pipeline as a first-class doctype.
- Transcription (Whisper or similar, local or API) → LLM pass that extracts: client concerns, decisions made, action items (with assignee + due date), follow-up questions, sentiment notes.
- Notes stored as a structured row in a per-client collection alongside the original audio + transcript.

### Meeting as a first-class doctype

- New `meetings` table in `structured_store` (or a typed view over a generic `events` table). Columns: client, date, attendees, duration, transcript_doc_id, notes_doc_id, action_items (list), decisions (list).
- Action items become their own queryable thing — open vs closed, assigned to whom, overdue.

### `prep_for_meeting(client, when)` MCP tool

- Single highest-leverage advisor tool. Bundles into one response:
  - Last meeting notes (decisions, open action items)
  - Recent portfolio activity since last meeting (drift, new positions, P&L moves)
  - Any flags from the firm's attached expertise pack (see [EXPERTISE_ROADMAP.md](EXPERTISE_ROADMAP.md))
  - Upcoming corporate events on held symbols (from `get_corporate_events`)
- Output is a one-page brief the advisor reads on the way to the meeting.
- The demo sentence: advisor opens Claude Desktop, says *"prep me for my 2pm with the Hendersons"*, gets a brief in 10 seconds.

### `get_meeting_notes(client, date_range)` and `list_action_items(client, status)`

- Smaller utility tools the agent can compose. Used by `prep_for_meeting` internally and by ad-hoc questions.

---

## v4.5 — Client profile object & advisor analytics

With ingestion fidelity solid, enrichment feeds available, and meetings captured, these become small additions rather than new systems.

### Client profile as a structured object

- New typed structure attached to each client collection: risk tolerance, time horizon, goals, household composition, IPS targets (allocation bands, max concentration, prohibited holdings), tax situation, liquidity needs.
- Editable in the frontend (form, not freeform markdown — this is the typed cousin of the v4.3 collection guide).
- Surfaced in full in `get_collection_info()` and as a short summary in every `search_collection` response on that collection.

### `compare_holdings_to_profile(collection_id)`

- Joins the (rolled-up) holdings table against the profile. Flags drift: out-of-band asset class allocation, concentration violations, prohibited holdings, tax-inefficient placement, cash drag.
- Composes naturally with `compute_portfolio_metric` and the v4.2 enrichment feeds.

### `run_monte_carlo(collection_id, params)`

- Forward projection on current holdings + cash flows + assumed returns/vol. Returns percentile bands.
- Probably starts with a simple parametric model; can layer historical bootstrapping later.
- Read-only — does not modify the collection.

### Additional `compute_portfolio_metric` metrics

- Max drawdown (needs price history from v4.2)
- Factor exposure (needs classification from v4.2)
- Tax-lot drift / harvest candidates
- Yield-to-maturity rollup for fixed income
- Effective duration

---

## v4.6 — Distribution (deferred from old v4.2)

Getting Asymptote into the places agents already live. **Deferred until at least one advisor firm is using the v4.1–v4.5 stack daily.** Distribution doesn't matter without product-market fit at one customer first.

When it's time:

- **Claude Desktop:** JSON snippet generator already exists (`build_mcp_export_payload`). Polish copy-paste flow + README.
- **Cursor / VS Code:** generator already emits `.vscode/mcp.json` — verify against current Cursor MCP spec.
- **OpenClaw:** minimal skill wrapping the MCP client, dropped into [openclaw/clawhub](https://github.com/openclaw/clawhub).
- **ChatGPT:** once OpenAI's connector API for custom tools stabilizes, add an export profile.
- **Hosted demo collection:** read-only public MCP endpoint pointed at SEC filings / arxiv. Marketing + docs in one.
- **Installer improvements:** single-binary Windows build, Homebrew formula, `pipx install asymptote`.

---

## v4.7 — Ingestion depth (the rest of old v4.3)

PDF table extraction was promoted into v4.1 P0.8 because it's load-bearing for advisor files. The remainder:

### URL / online document support

- Accept URLs alongside file uploads. Store `source_url` + `last_refreshed`. "Refresh from URL" button. Optional periodic refresh per collection. Auth: bearer / basic for private URLs.

### Email ingestion

- `.eml` / `.msg` / Gmail MBOX import. Strip signatures + quoted history at ingest. Thread-level grouping so reply + parent end up adjacent in retrieval. Email is where half the client context lives — pairs naturally with v4.4 meeting capture.

### Semantic / structure-aware chunking

- Paragraph- or heading-bounded chunks for PDFs and markdown. Recursive chunking respecting document hierarchy. Topic-boundary chunking (LLM-driven, expensive, gated behind a setting).

---

## v5 — Speculative, in rough priority

Items that aren't funded yet but belong in the same direction of travel.

### Two-way custodian / CRM integration

- The big one. Pick ONE custodian (whichever the first advisor firm uses) and build read-only API ingestion for holdings, transactions, client records → `structured_store`.
- Read-only first. Write-back (open accounts, place trades, update CRM records) only after 6+ months of stable read flows.
- Candidate first targets: Schwab Advisor API, Wealthbox, Redtail.

### Stock screener tool

- `screen_securities(criteria)` — generic screener over the user's universe + an external data feed. Lower priority than the enrichment feeds in v4.2; needs paid market data to be genuinely useful.

### Query enhancement at the MCP layer

- A `rewrite_query` step inside `search_collection` that uses a small LLM to reformulate before retrieval. Trades latency for recall. Toggleable per-request.

### Multi-hop retrieval

- `search_multi_hop(question, max_hops=3)` chaining searches internally. Saves round-trips and gives Asymptote control over join logic.

### Multi-user mode

- Either fully implement user accounts / RBAC / per-user collections, or delete the half-built config option. Has been on the list since v2 — still dead weight until a firm needs more seats.

### Hosted multi-tenant deployment

- Only after a single-firm deployment is rock-solid. Financial-services compliance (SEC, FINRA, SOC2) is the long pole — months of work before any value ships. Don't start without a paying customer asking.

### Enterprise features (gated behind demand)

- Audit logging, SSO (SAML/OAuth), API rate limiting per MCP token, document retention policies. Not pursuing until a paying customer asks.

### Performance & scaling

- GPU embedding generation (batch uploads), Postgres + pgvector backend, Redis cache for repeated queries. Worth doing only when a user hits the wall on FAISS single-index performance.

---

## Technical debt

1. **Test coverage.** Especially the new ingestion-fidelity work — see v4.1 P0.7. Every MCP tool should also have a happy-path and error-path test driven through the FastMCP client.
2. **Type completeness.** [services/mcp_server.py](services/mcp_server.py) is well-typed; extend the same rigor to [services/upload_service.py](services/upload_service.py) and [services/indexing/indexer.py](services/indexing/indexer.py).
3. **Structured error codes for MCP.** Errors currently raise `ValueError` with a message. A thin error-code taxonomy (`collection_not_found`, `document_not_found`, `sql_invalid`, `ingestion_failed_no_header`, etc.) would let hosts retry intelligently instead of parsing prose.
4. **Centralized settings validation.** [config.py](config.py) has grown organically — one pass to consolidate and document each setting.
5. **Logging.** Structured JSON logging behind a setting; current logs are grep-friendly but not machine-friendly.
6. **`_FORBIDDEN_KEYWORDS` audit.** `REPLACE` was removed this session. Re-audit the rest against actual SQLite docs — confirm we're only blocking DDL/DML/admin, not legitimate string/math/window/CTE/aggregate functions.

---

## How to use this file

- **v4.1 is the only thing that matters right now.** Don't start anything below it until P0.1–P0.7 are done. The advisor demo bugs above are the acceptance test: re-run those three questions against the same Schwab file and they should produce correct numbers without manual workaround SQL.
- **v4.2 is next** — small, contained tools that each independently unlock a real advisor question.
- **v4.3 is the previously-planned MCP polish work**, still valid, just deferred.
- **v4.4 / v4.5** are the advisor-workflow wedge that turns this into a product, not a query layer.
- **v4.6 / v4.7** wait until there's daily usage at one firm.
- **v5** is "don't build yet, but if someone asks, this is the shape."
- **Technical debt** is background tax — chip away whenever touching adjacent code.

**Last updated:** 2026-04-11

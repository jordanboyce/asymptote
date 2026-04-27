# Asymptote Roadmap

Forward-looking work for Asymptote. Items that have already shipped are not listed here — check the git log for history.

---

## Strategic frame

Asymptote's primary product surface is the **in-app chat** inside the Vue frontend. An advisor installs Asymptote, pastes an Anthropic or OpenAI API key into settings, uploads their files, and starts asking questions — no Claude Desktop, no Cursor, no MCP configuration. The MCP endpoint ([services/mcp_server.py](services/mcp_server.py)) stays supported as a secondary surface for power users who already live in an external MCP client.

**We do not rebuild the chat app.** The in-app chat is a thin adapter on top of the Anthropic Messages API (native tool use) and OpenAI Responses API (function calling). We reuse their SDKs for streaming, conversation state, and tool-call orchestration. Our job is to translate the existing tool registry into the provider's tool schema, run the standard tool-use loop on the backend, and stream tokens to the frontend. Every hour spent building chat primitives is an hour not spent on what Asymptote actually owns.

**What Asymptote is:** the trustworthy, privacy-preserving data layer that makes a user's own documents usable by whatever LLM they already trust — reached through Asymptote's own chat UI by default, or through an external MCP client when the user prefers one. The intelligence layer lives upstream in Anthropic / OpenAI. Asymptote owns:

1. **Ingest arbitrary tabular and document data** from arbitrary tools (brokerages, banks, CRMs, planning software, internal systems) and make every file faithfully agent-queryable, regardless of vendor or column naming convention.
2. **Return PII-free and CUI-free context.** Every tool response — whether served to the in-app chat or to an external MCP client — is redacted before it leaves the process (see P0.0). No personal identifier, no account number, no Controlled Unclassified Information element reaches an external LLM. This is the feature — without it, regulated users (financial advisors, federal contractors, healthcare, legal) cannot use any external LLM against their data at all.
3. **Pass raw data through by default; only abstract when we must.** Column headers, row values, document text are returned as-is (modulo redaction) so the calling LLM does its own semantic translation. Role mapping and other semantic layers exist only where *Asymptote itself* has to act deterministically — aggregations, metric computation, routing — not as a translation step for the LLM.
4. **Expose analytical and enrichment primitives as a single tool registry** callable from both surfaces — the in-app chat (via the provider tool-use loop) and external MCP clients. One set of primitives; two delivery surfaces; no feature drift between them.
5. **Never be silently wrong.** A data-layer tool that returns confidently incorrect numbers is worse than no tool. Aggregations, type coercions, and unit conversions must surface uncertainty rather than collapse it. When role detection fails, degrade to raw-data tools and let the LLM handle semantics — never guess and pretend.

Financial advisors are the first wedge, but the architecture is general — any tabular export from any tool should land cleanly, and any regulated user who needs PII/CUI scrubbing before sending context to an external model is a target user.

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

> **Gating note:** P0.0 (PII Redaction) must be implemented and verified before any feature that touches LLM tool responses, chat context, or search results. P0.1–P0.8 can proceed in parallel for local ingest logic, but no LLM-facing output path ships without P0.0 in place. P0.5 (LLM-assisted column inference) is explicitly blocked on P0.0 because it sends column names and sample values to an external model.

### Status snapshot

| Item | Status | Notes |
|---|---|---|
| P0.0 — PII redaction | ✅ Shipped | `services/privacy/` module, Presidio engine, middleware, four MCP tools, SQLite audit log, regression test |
| P0.1 — Header detection | ✅ Shipped | Integrated in [document_extractor.py](services/document_extractor.py); 5 read sites wired |
| P0.2 — Numeric coercion | ✅ Shipped | `_parse_generic_number` (commas, parens, K/M/B), `_parse_date_like`, 80% threshold, `__raw` sibling columns |
| P0.3 — Lot rollup | ✅ Shipped | Auto-created `<table>__by_symbol` VIEW; `compute_portfolio_metric` uses it when `group_by_symbol=True` |
| P0.4 — Vendor profiles | ✅ Shipped | Profile framework + YAMLs for Pershing / Schwab / Fidelity / Vanguard / NetX360; Pershing UGL column hints corrected; NetX360 HBIL hierarchical preprocessor (`services/ingest_profiles/netx360.py`) flattens multi-account exports |
| P0.5 — LLM role inference | 🟡 Partial | `services/llm_role_inference.py` exists; provenance field (`profile`/`heuristic`/`llm`) on schema columns needs verification |
| P0.6 — Numeric sanity guards | ❌ Open | Not implemented |
| P0.7 — Regression suite | 🟡 Partial | 5 of 5 fixtures + [tests/test_ingest.py](tests/test_ingest.py) green (57/57 passing). NetX360 HBIL fixture added. Remaining: snapshot-based assertions per fixture |
| P0.8 — PDF table extraction | ❌ Open | Not started |

### P0.0 — PII Redaction Layer (Presidio) — ✅ Shipped

**Priority:** Highest — this is a prerequisite for shipping anything to production. No identifiable data can leave Asymptote when content is sent to external LLM providers (Claude, ChatGPT, Google, or any other). This is especially critical for financial data, which routinely contains account numbers, Social Security numbers, names, and other highly sensitive identifiers.

**Why this is P0.0 and not later:**
- Every MCP tool response, every search result, and every chunk of context that reaches the calling LLM is a potential PII leak.
- Financial advisors upload real client data — real account numbers, real names, real balances. Any of that reaching an external model is a compliance violation and a trust violation.
- Presidio is open-source (MIT license), runs 100% locally, and requires no cloud service for detection. There is no legitimate reason to defer this.

**What counts as PII — be exhaustive, not approximate:**

Asymptote must redact (at minimum) all of the following before any content leaves to an LLM:

| Category | Examples |
|---|---|
| Personal identifiers | Full name, first name, last name, initials used as identifiers |
| Government IDs | SSN, EIN, ITIN, driver's license number, passport number, national ID |
| Financial account identifiers | Account number, routing number, IBAN, credit card number, debit card number, brokerage account ID |
| Contact information | Phone number, email address, mailing address, ZIP+4, PO Box |
| Digital identifiers | IP address, MAC address, username, user ID, device ID, cookie/session token |
| Biometric data | Any text reference to biometric identifiers (fingerprint ID, face ID hash) |
| Medical / health identifiers | Medical record number, health plan beneficiary number, diagnosis code when paired with a name |
| Dates linked to individuals | Date of birth, date of death, admission/discharge dates |
| Social media | Handles, profile URLs, screen names |
| Financial-specific PII | CUSIP when appearing in context with account owner info, brokerage-specific client IDs, relationship manager IDs, rep codes tied to individuals |
| Free-text PII | Any of the above embedded in notes, memos, or document chunks |

When in doubt, redact. False positives (redacting something that wasn't PII) are far less harmful than false negatives (leaking something that was).

**Architecture — new module: `services/privacy/`**

```
services/privacy/
  __init__.py
  redaction_engine.py     # Core Presidio wrapper — analyze + anonymize
  redaction_middleware.py # Wraps all MCP tool output before it exits Asymptote
  redaction_config.py     # Per-collection profiles, allow-lists, custom recognizers
  redaction_log.py        # Audit log writer and reader
  custom_recognizers/
    financial_account.py  # Custom PatternRecognizer for brokerage-specific IDs
    cusip_in_context.py   # CUSIP + account owner = PII; CUSIP alone = not PII
```

**Core implementation — `redaction_engine.py`:**

- Wrap `presidio_analyzer.AnalyzerEngine` and `presidio_anonymizer.AnonymizerEngine`.
- Configure the analyzer with all standard Presidio recognizers (covers PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, IBAN_CODE, IP_ADDRESS, US_SSN, US_DRIVER_LICENSE, US_PASSPORT, US_BANK_NUMBER, DATE_TIME, LOCATION, NRP, and more).
- Add custom `PatternRecognizer` instances for financial-specific identifiers not in the default set (see `custom_recognizers/`).
- `redact_text(text: str, collection_id: str | None = None) -> RedactionResult` — analyzes and anonymizes a string, returns both the redacted string and the full list of `RecognizerResult` instances (entity type, start, end, score, replacement token).
- `redact_structured(rows: list[dict], collection_id: str | None = None) -> RedactionResult` — walks every string-valued field in every row, applies `redact_text` cell by cell, returns the redacted rows alongside a per-cell audit trail.

**Redaction is at the OUTPUT boundary, not at ingest:**

- The original unredacted data lives in the local store on the user's machine. It is never transmitted anywhere.
- Redaction happens in `redaction_middleware.py`, which wraps the return value of every MCP tool response and every chunk of context assembled for the calling LLM.
- The call site is immediately before data exits `mcp_server.py` — not inside `structured_store.py` or `document_extractor.py`. This preserves the ability to run exact-match SQL and full-text search against the real data locally, while guaranteeing nothing identifiable escapes outward.
- Hook point: `redaction_middleware.py` integrates with the existing plugin/hook pattern in `structured_store.py`. Any tool response that passes through the MCP layer is automatically intercepted.

**Configurable redaction styles (per-collection, per-entity-type):**

| Style | Example output | When to use |
|---|---|---|
| `[REDACTED]` | `[REDACTED]` | Default — maximum anonymity, opaque |
| `[ENTITY_TYPE]` | `[ACCOUNT_NUMBER]`, `[PERSON]` | Default for most financial use cases — tells the agent *what* was removed without revealing the value |
| `consistent_pseudonym` | `John Smith` → always `Alex Morgan` within a session | When the agent needs to reason about relationships ("the same person") without seeing real names |
| `partial_mask` | `****1234` for account numbers | Useful for human review UIs where the last 4 digits confirm context |
| `synthetic_placeholder` | Replaced with a random-but-valid-format value | For testing pipelines end-to-end with realistic-looking fake data |

The active style is configured in the collection's redaction profile (`redaction_config.py`). Financial collections default to `[ENTITY_TYPE]`; general-document collections default to `[REDACTED]`.

**Auditability — every redaction is logged:**

Each redaction event writes a structured log entry to a local SQLite table (`redaction_log`):

```
redaction_log
  id            INTEGER PRIMARY KEY
  timestamp     DATETIME
  session_id    TEXT       -- groups all redactions in one agent session
  collection_id TEXT       -- which collection the data came from
  tool_name     TEXT       -- which MCP tool produced the output
  document_id   TEXT       -- source document, if traceable
  entity_type   TEXT       -- e.g. ACCOUNT_NUMBER, PERSON, US_SSN
  original_text TEXT       -- the actual PII that was found (stored locally only)
  replacement   TEXT       -- what it was replaced with
  start_char    INTEGER    -- character offset in the source text
  end_char      INTEGER    -- character offset in the source text
  score         REAL       -- Presidio confidence score
```

`original_text` is stored in the local audit log so users can review exactly what was redacted. It never leaves the machine.

**Redaction preview mode:**

Before sending any tool response to the LLM, Asymptote can surface a preview of what will be redacted. This is exposed as:

1. A "review before sending" flag in the collection settings — when enabled, the MCP tool response is held and the redaction summary is surfaced in the frontend (collection settings panel) before the response is released to the calling agent.
2. An MCP tool/resource `get_recent_redactions(session_id, limit=50)` that returns the redaction log for the current session in human-readable form — entity types found, replacements applied, which tool and document they came from. The calling agent (or the user reviewing the agent session) can inspect this at any time.

**MCP exposure:**

- `get_recent_redactions(session_id: str | None = None, collection_id: str | None = None, limit: int = 50) -> list[RedactionSummary]` — MCP tool returning what was redacted in this session. Agents can call this to self-verify: "confirm what PII was removed before I proceed."
- `get_redaction_config(collection_id: str) -> RedactionProfile` — returns the active redaction profile for a collection (style, allow-list, active recognizers).
- Optionally, expose `redaction://session/{session_id}` as an MCP resource for host UIs that want to render a live redaction feed.

**Per-collection redaction profiles (`redaction_config.py`):**

Each collection can define:
- `redaction_style` — which replacement style (see above)
- `entity_types_enabled` — which Presidio entity types to enforce (default: all; can restrict for non-financial collections)
- `allow_list` — strings that match PII patterns but are known safe (e.g., `"Fidelity"` matches a person-name pattern; firm names, fund names, and ticker symbols that look like abbreviations go here)
- `custom_recognizers` — additional `PatternRecognizer` instances for this collection's domain (e.g., a specific CRM's internal ID format)
- `minimum_score_threshold` — Presidio confidence floor before a match is treated as PII (default: 0.5; financial collections should use 0.4 to be conservative)
- `strict_mode` — when `true`, any Presidio match above the threshold is redacted even at low confidence; when `false`, only high-confidence matches are redacted. Default `true` for financial collections.

**Custom recognizers for financial-specific PII:**

Presidio's default recognizer set covers common US/EU PII well, but financial workflows have identifiers not in the default set:

- `FinancialAccountRecognizer` — regex + context clues for brokerage account numbers (typically 8–12 digits, often preceded by "Account #", "Acct", or a custodian name)
- `RoutingNumberRecognizer` — 9-digit ABA routing numbers; distinct from SSNs (same digit count) by context and checksum
- `CUSIPInContextRecognizer` — CUSIP alone (9-char alphanumeric) is a security identifier, not PII; CUSIP appearing within N tokens of an account holder name or account number is treated as PII-adjacent and redacted in that context
- `BrokerageClientIdRecognizer` — configurable per-vendor regex (e.g., Pershing's rep code + client number format); added via collection profile's `custom_recognizers`

**Presidio runs locally — no cloud dependency:**

- `presidio-analyzer` and `presidio-anonymizer` install as Python packages (`pip install presidio-analyzer presidio-anonymizer`).
- The default NLP model is `en_core_web_lg` from spaCy — downloaded once on first run, cached locally.
- No network call is made during analysis or anonymization. PII detection is fully air-gapped from the redaction engine's perspective.
- Add `presidio-analyzer`, `presidio-anonymizer`, and `spacy` (with `en_core_web_lg`) to `requirements.txt` / `pyproject.toml`.

**Integration checklist:**
- [ ] `services/privacy/` module scaffolded with the structure above
- [ ] `redaction_engine.py` wrapping Presidio with all standard + custom recognizers
- [ ] `redaction_middleware.py` intercepting all MCP tool responses before they exit `mcp_server.py`
- [ ] `redaction_log` SQLite table created and populated on every redaction event
- [ ] `get_recent_redactions` MCP tool implemented and tested
- [ ] Per-collection redaction profiles with allow-list and style configuration
- [ ] Custom financial recognizers (account numbers, routing numbers, CUSIP-in-context)
- [ ] Redaction preview mode wired into the frontend collection settings panel
- [ ] `en_core_web_lg` added to setup/install instructions
- [ ] Regression test: a fixture containing known PII (fake but realistic) runs through the full MCP path and none of the PII appears in the tool response

### P0.1 — Smart header detection — ✅ Shipped

**Problem:** Brokerage/bank/CRM exports almost always have N preamble rows before the actual header. Current importer assumes row 1 is the header.

**Implementation:**
- In [services/document_extractor.py:550](services/document_extractor.py#L550) (and the two other `pd.read_csv` call sites at lines ~613 and ~721), wrap the read in a header-sniffing pass.
- Heuristic: read the first ~30 rows raw. Find the first row where (a) most cells are non-empty, (b) cells are short strings without numeric/currency formatting, (c) the row immediately below has predominantly numeric/date values. Promote that row to header, drop the rows above as preamble metadata.
- Preserve the dropped preamble as `document_metadata` (`as_of_date`, `currency`, `view_type`, etc.) — useful context for the agent.
- Fall back to `pd.read_csv` defaults if no candidate row is found.
- Same logic applies to XLSX sheets.

### P0.2 — Numeric coercion for currency / accounting strings — ✅ Shipped

**Problem:** `"1,591.20"`, `"$1,591.20"`, `"(123.45)"`, `"1.5K"` all currently land as TEXT. Downstream `CAST(... AS DOUBLE)` silently coerces to wrong values.

**Implementation:**
- During structured ingest in [services/structured_store.py](services/structured_store.py), after header detection, for each column attempt a numeric coercion pass: strip currency symbols, thousands separators, accounting-negative parens, common suffixes (`K`, `M`, `B`). If ≥80% of non-null values coerce cleanly, store the column as REAL not TEXT.
- Record the original raw string in a sibling `_raw` column (or in the column's metadata) so the agent can still see the source if needed.
- Apply the same pass to date-like columns (`Trade Date`, `Settle Date`, `Maturity`) → ISO date strings or DATE.

### P0.3 — Lot / row rollup as a first-class concept — ✅ Shipped

**Problem:** Every brokerage file represents a single position as N rows (one per tax lot). Lots are an implementation detail; no human or agent should care about them.

**Implementation:**
- When a `Symbol` / `CUSIP` / `Security ID` role is detected, automatically expose a logical view alongside the raw table: `<table_name>__by_symbol` (or `positions_by_symbol`) that aggregates market value, cost basis, quantity, gain/loss, and weighted-average unit cost.
- Add a `group_by_symbol: bool = True` parameter to `compute_portfolio_metric` that rolls up lots before computing.
- The raw lot-level table stays available for tax/cost-basis questions that genuinely need it.

### P0.4 — Known-vendor schema profiles — 🟡 Partial

**Problem:** Schwab, Fidelity, Vanguard, Pershing, Raymond James, etc. each export the same logical concepts under different column names and layouts. Heuristic role detection works on common cases but misses the long tail.

**Implementation:**
- New `services/ingest_profiles/` directory containing per-vendor YAML/JSON profiles. Each profile declares: file signature (filename glob, presence of telltale strings, distinctive column set), preamble row count, header row offset, column → semantic role map, type overrides, doc-level metadata extractors.
- At ingest time, run profile detection first. If a profile matches, apply it deterministically. Otherwise fall back to P0.1/P0.2/role-from-name heuristics.
- Ship initial profiles for **Pershing first** (see below), then Schwab (Holdings, Unrealized G/L, Realized G/L, Transactions), Fidelity (Positions, History), and Vanguard (Holdings, Activity).
- Profiles are plain data files — easy for users (or future you) to add new vendors without code changes.

#### Pershing / NetX360 profiles (first priority — first advisor customer)

Two distinct export formats analyzed from real advisor files:

**Pershing Unrealized Gain/Loss (flat CSV)**
- Exported as `Unrealized+Gain+Loss_<account>.xlsx - ExportExcel.csv`
- 9 preamble rows: title, blanks, single-cell "Key: Value" metadata (`Quote Type`, `Cash Included`, `View type`, `All values in USD`, `As Of` timestamp)
- Header at row 9: 20 columns — `Security Description`, `Security Identifier`, `Quantity`, `Projected Annual Income`, `Current Yield`, `Gain/Loss`, `Gain/Loss %`, `Trade Date`, `Unit Cost`, `Last Price`, `Market Value`, `Tax`, `Current Total Cost`, `Security Type`, `% of Portfolio`, `Symbol`, `Original Quantity`, `Original Total Cost`, `Original Adjusted Cost`, `Interest`
- P0.1 header detection already handles this correctly. Profile adds: column → semantic role map, type overrides, sentinel detection.
- **Numeric quirks (P0.2):** comma-thousands (`"1,591.20"`), parenthetical negatives (`"(408.80)"`), `"-"` for unavailable, `"Provide"` for missing cost basis
- **Lot-level rows:** same symbol appears N times (one per tax lot); rolled-up rows use `"Multiple"` as trade date
- **Footer:** `TOTAL` row followed by blank rows and multi-paragraph `Disclaimers` section — must be stripped before ingest
- File signature: first row contains `"Unrealized Gain Loss"`, or filename matches `Unrealized*Gain*Loss*`

**NetX360 "Holdings by Investor" (hierarchical CSV — needs pre-processor)**
- Exported as `HBIL<id>.csv` from NetX360 platform
- **Not flat tabular data.** Hierarchical multi-account report that repeats per account:
  1. `Account Name,,,Account Number,,Account Type` row
  2. Optional insurance/annuity metadata block: product name, carrier, status, policy values (Cost Basis, Death Benefit, Surrender Value, FWA, etc.), policy dates (issue, maturity, surrender expiration), party info (SSN, DOB, address), beneficiaries
  3. `ASSET,,,,,,,,TICKER,,ASSET TYPE,,MGT. NAME,,QUANTITY,,PRICE ($),VALUE ($)` header row
  4. Asset data rows (position-level, no tax lots)
  5. `Account Total:,,,,,,,,,,,,,,,,,,"$X"` row
- Final `total` row at bottom
- Account types observed: Transfer On Death (Individual), IRA, General, Trusts, Trust, Retirement Account IRA
- Insurance products: variable annuities (Lincoln, Forethought), equity indexed annuities (Pacific Life)
- P0.1's flat header detection **cannot handle this** — multiple `ASSET` header rows interspersed with account metadata
- **Profile must implement a pre-processing step** that:
  1. Detects the NetX360 signature: row 0 contains `"Report Type"`, row 1 contains `"Holdings by Investor"`
  2. Iterates account sections by scanning for `Account Name` rows
  3. Extracts account metadata (name, number, type) and optionally insurance/policy info
  4. Collects asset rows from each section
  5. Flattens into a single table with added columns: `Account Name`, `Account Number`, `Account Type`
  6. Strips `Account Total` and final `total` rows
- File signature: filename matches `HBIL*`, or first two rows contain `"Report Type"` and `"Holdings by Investor"`
- Test fixture: `tests/fixtures/ingest/netx360_holdings_by_investor.csv`

### P0.5 — LLM-assisted column role inference (narrow, for deterministic aggregation only) — 🟡 Partial

> **Blocked on P0.0.** This feature sends column names and sample cell values to an external LLM. Sample values may contain PII/CUI (account numbers embedded in column headers, names in the first data row, etc.). P0.0's redaction layer must be applied to the sample values before they leave Asymptote. Do not ship P0.5 until P0.0 is verified end-to-end.

> **Scope reframed:** Original plan called for an upload UI where the user accepts/edits/rejects LLM-proposed role mappings. That's been **cut** — see reasoning below.

**What this is for and what it isn't:**

The calling LLM (Claude, ChatGPT) will translate cryptic column headers on its own when it sees the data. `Hldg_USD` → "market value in USD" is trivial for a frontier model; we don't need to pre-solve that for it. Role inference earns its keep in exactly one narrow place: when an MCP tool in Asymptote has to act on column semantics deterministically without an LLM in the loop — specifically `compute_portfolio_metric` and any future aggregation primitives that take a metric name (`top_holdings`, `breakdown_by_sector`, etc.) rather than raw SQL.

For every other tool (`query_table`, `get_table_rows`, `aggregate_table`, `search_collection`), raw column headers go through untouched and the calling LLM handles semantics. That surface is already role-agnostic — [mcp_server.py:803-809](services/mcp_server.py#L803-L809) already instructs the LLM to fall back to `aggregate_table`/`query_table` when `financial_roles` is absent.

**Implementation (revised):**
- Keep the existing ingest-time LLM inference pass at [services/llm_role_inference.py](services/llm_role_inference.py), gated behind `enable_llm_schema_inference` (already done). Runs only when ≥threshold of columns are still unmapped after vendor profiles and heuristics.
- Presidio-redact all sample values before transmission (already done) — wire `collection_id` through from [services/structured_store.py:596](services/structured_store.py#L596) so per-collection Presidio profiles apply.
- **No accept/edit/reject UI.** Users don't review role mappings. If the LLM guesses wrong, the only blast radius is `compute_portfolio_metric` returning wrong numbers for that table — which is unacceptable, so instead:
- **Confidence-gated application with fail-closed semantics.** When confidence is below threshold or inference fails, leave `role` null for that column. `compute_portfolio_metric` already checks for required roles and returns a structured error when absent — the calling LLM falls back to `query_table` and computes the metric itself from raw data. This is the "degrade to raw-data tools, never guess and pretend" rule from the strategic frame.
- **Surface inferred-vs-detected provenance in `get_table_schema`.** Each role should carry a source: `"profile"`, `"heuristic"`, `"llm"`, or null. The calling LLM can then choose to trust or discount LLM-inferred roles when deciding whether to call `compute_portfolio_metric`.
- Profile bootstrapping (LLM resolutions becoming candidate profiles for future files) is deferred — only revisit if long-tail unknown vendors become a real usage pattern.

**Audit task before declaring P0.5 done:**
- Confirm every MCP tool other than `compute_portfolio_metric` returns correct data when `financial_roles` is empty. `query_table`, `get_table_rows`, `aggregate_table`, `search_collection`, `get_document_context` should all be role-agnostic. Any place that silently depends on a role is a bug.
- Add a test that ingests a file with completely unknown headers and verifies (a) `query_table` returns correct rows with raw headers, (b) `compute_portfolio_metric` returns a structured "no role detected, use query_table" error rather than an empty result or a wrong number.

### P0.6 — Numeric sanity guards on aggregates — ❌ Open

**Problem:** A `compute_portfolio_metric` or aggregated SQL query that returns nonsensical values (market values < $10 next to gain/loss > $100, weights summing to 12%, NULL leakage in critical columns) should never be returned silently.

**Implementation:**
- After computing any aggregate, run a small set of sanity checks: weights sum to ~100%, market values within plausible orders of magnitude given cost basis, no NULLs in canonical roles when raw data has values, types match expected.
- On failure, attach `warnings: ["aggregation_likely_lost_precision: column X looks numeric but is stored as TEXT"]` to the response. Don't suppress the result, but never ship it without the warning.
- Same guard fires inside the SQL execution path in [services/structured_store.py](services/structured_store.py) — if a query SUMs a TEXT column that contains digit+comma values, warn.

### P0.7 — Regression suite of real exports — 🟡 Partial (tests green; NetX360 HBIL fixture still missing)

**Problem:** Without test fixtures of real broker/bank exports, every fix to the ingestion path risks breaking another vendor's format.

**Implementation:**
- Create `tests/fixtures/ingest/` with at least one anonymized export per supported vendor. Real-shaped data, fake names/account numbers.
- **Already started:** `tests/fixtures/ingest/pershing_unrealized_gl.csv` (Pershing flat G/L). Next: NetX360 Holdings by Investor, Schwab Unrealized G/L, Fidelity Positions, Vanguard Holdings.
- For each fixture, snapshot the expected: detected header row, column count, role map, type map, and a few canonical aggregates from `compute_portfolio_metric`.
- CI runs the full ingestion path against every fixture and asserts the snapshot. Any drift requires explicit acceptance.
- This is the only thing keeping the "never silently wrong" bar honest over time.

### P0.8 — PDF table extraction (promoted from old v4.3) — ❌ Open

**Problem:** Many custodians ship statements as PDF, not CSV. Today there's no path from a PDF statement to `structured_store`.

**Implementation:**
- Detect tables in PDFs (candidate libraries: `camelot`, `tabula-py`, or an LLM vision pass) and route them through the same header-detection / numeric-coercion / role-mapping pipeline as CSV.
- Same vendor profiles apply — a Schwab PDF statement should land identically to a Schwab CSV.
- Charts/figure extraction stays out of scope for now.

### Status of items already in the previous v4.1 batch

The collection-guide / `find_in_documents` / `rows_jsonl` / `identifier` disambiguation / `suggested_next_tools` items are still valid and pair naturally with the new ingestion work, but they move to **v4.3 (MCP surface polish)** below. None of them matter if the underlying data is wrong.

**Already shipped:**
- `REPLACE` removed from `_FORBIDDEN_KEYWORDS` ([services/structured_store.py:224](services/structured_store.py#L224)) so agents can use SQLite string functions to clean numeric strings as a workaround until P0.2 lands.
- **P0.1 (Smart header detection):** Implemented in [services/document_extractor.py](services/document_extractor.py). Header-sniffing pass using `csv` stdlib scans up to 30 raw rows, finds the first row that looks like text labels followed by a data-like row. Wired into all 5 tabular read sites (`_extract_csv`, `extract_csv_rows`, `extract_tabular_sheets` CSV + XLSX branches, `_extract_xlsx`). Preamble metadata extracted as key/value pairs (supports both `Key,Value` two-cell and `Key: Value` single-cell styles) and surfaced as `document_metadata` on the sheet dict. Verified against Pershing Unrealized G/L (header at row 9) and clean CSVs (no regression). First test fixture saved at `tests/fixtures/ingest/pershing_unrealized_gl.csv`.

---

## v4.2 — Enrichment data feeds as MCP tools — ✅ Shipped

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

## v4.3 — MCP surface polish (shipped)

All five items below shipped together — small, contained improvements to the calling-LLM experience. Regression coverage: [tests/test_v43_mcp_polish.py](tests/test_v43_mcp_polish.py).

### Per-collection guide memory — shipped

User-editable markdown blob per collection. Stored on the `collections` table in a new `guide TEXT` column (migration in both SQLite and Postgres backends). Full text returned by `get_collection_info()`; a ≤500-char summary is inlined into every `search_collection` response as `collection_summary.guide_summary` so it travels with retrieval. Frontend textarea wired into the existing Edit Collection modal in [frontend/src/App.vue](frontend/src/App.vue).

**Why it paid off:** agents using Claude Desktop lose context between sessions. A guide that travels with every tool response is the cheapest way to give Asymptote durable memory. Next improvement (deferred): default-template seeding on collection creation.

**Cross-link with v4.6:** the client profile object below is the structured cousin of this. The guide is freeform markdown ("how to think about this collection"); the profile is typed fields ("risk_tolerance: moderate"). They coexist — the guide is for narrative, the profile is for primitives.

### `find_in_documents(pattern, case_sensitive=False, collection_id=None, max_results=20)` — shipped

New MCP tool at [services/mcp_server.py](services/mcp_server.py). Does a literal substring scan against every indexed chunk via `metadata_store.get_all_chunks_ordered()`. Returns filename, page, chunk_id, character offset, and an excerpt with the match wrapped in `«…»`. Docstring positions it as the right tool for exact strings, ticker symbols, CUSIPs, identifiers, and quoted phrases — contrasted against `search_collection` for concepts. The `literal` parameter is reserved for future regex support (currently always literal).

### `rows_jsonl` → structured `rows` output — shipped

`search_collection` now emits `structured_tables[].columns` + `structured_tables[].rows` (list of lists, display-name headers) for every inlined small table. `rows_jsonl` is kept as a fallback for inlined tables with more than 50 rows so very wide × tall tables don't bloat the response when a JSONL representation is more compact. Helper: new `render_table_as_rows()` in [services/structured_chat.py](services/structured_chat.py), used by the existing `render_table_as_jsonl()`.

### Disambiguate the overloaded `identifier` parameter — shipped

`get_table_schema`, `get_table_rows`, `compute_portfolio_metric`, and `aggregate_table` all now accept `identifier_type: Literal["table_name", "filename", "document_id"] | None = None`. When provided, `StructuredStore.get_schema` restricts the SQL lookup to that one column instead of searching all three. Default (`None`) is auto-detect — fully backwards compatible.

### `suggested_next_tools` hints in responses — shipped

`search_collection` detects numeric intent (`total`, `sum`, `average`, `mean`, `count`, `top N`, `breakdown`, etc. matched on word boundaries so "summarize" doesn't trigger "sum") and exact-match intent (double-quoted substrings, `verbatim`, `literal`, `exact`) in the query string. When detected, the response includes `suggested_next: [{tool, reason}, ...]` pointing at `list_tables` for numeric questions and `find_in_documents` for verbatim lookups. Pure bonus signal — hosts that ignore it still work.

---

## v4.4 — In-app chat surface (primary product surface) — ✅ Shipped

The wedge that turns Asymptote from "data layer behind an MCP endpoint" into "the tool advisors actually open every morning." Until this shipped, every demo required Claude Desktop or Cursor — a setup step that killed trial-to-usage conversion more than once.

**Non-goal: build a chat app.** We do not compete with Claude Desktop or ChatGPT on branching, regeneration, artifact rendering, image analysis, or any other chat-UX surface. We build the smallest possible chat that lets an advisor point at their collection and get the Asymptote tool set through a frontier model.

### Architecture

- **Backend chat adapter** (new module, `services/chat_adapter.py` or similar). Runs the provider's native tool-use loop:
  1. Take the conversation (list of messages) + a `tools` array derived from the existing MCP tool registry.
  2. Call the provider API (Anthropic Messages API or OpenAI Responses API, depending on which key the user configured).
  3. When the provider asks for a tool call, dispatch it to the existing tool function — same path, same redaction middleware.
  4. Append the tool result and loop until the provider stops asking for tools.
  5. Stream tokens back to the frontend via SSE.
- **No reimplementation of conversation state, streaming, or tool-call orchestration.** The `anthropic` and `openai` SDKs already ship these. Our job is the tool-registry translator and the dispatcher.
- **Conversation memory** = the messages array the provider consumes. No new storage format. If we later want conversation history beyond one session, persist the messages array as-is.
- **Per-collection tool scoping.** When a conversation is bound to a collection, the tool list filters to that collection's tables/documents. Smaller provider-side context; clearer tool-call behavior.
- **Unified tool registry.** Both surfaces (in-app chat and external MCP client) call into the same set of primitives. No forking, no feature drift.

### Configuration

- BYO-key settings panel in the frontend: Anthropic API key, OpenAI API key, default provider, default model, default collection.
- Keys stored locally (OS keyring or encrypted `.env`), never sent anywhere except the configured provider.
- Provider selection is per-conversation with a sensible default; switching providers mid-conversation is not supported in v1.

### UI

- Extend the existing chat tab in [frontend/src/App.vue](frontend/src/App.vue) rather than building a new page.
- Streaming tokens, tool-call indicators (cheap "calling `search_collection`..." status), and a collapsible redaction-summary panel so the advisor can see what was stripped before each outbound call.
- No thread branching, no regeneration, no message editing in v1 — these are chat-app features, not data-layer features.

### What shipped (2026-04-17)

- **`POST /api/chat/stream`** — SSE streaming endpoint that emits `tool_start`, `tool_end`, `thinking`, `text_delta`, `sources`, and `done` events as the agent works. The full native tool-use loop (Anthropic + OpenAI, up to 8 iterations) runs in this path with real-time feedback; Ollama/ReAct path also emits tool-start/end events.
- **Live tool-call cards** — As each tool fires, a card appears in the chat bubble with a spinning indicator and "running…" label. The card resolves with results when the call returns.
- **Streaming text** — The final response streams word-by-word into the bubble instead of appearing all at once after a 15–30 second wait.
- **Streaming cursor** — Blinking cursor at the end of in-flight text.
- **`POST /api/collections/{id}/brief`** — Dedicated REST endpoint calling `brief_generator.py` directly (no token cost).
- **`/brief` slash command** — Type `/brief` or click "Generate Meeting Brief" to instantly generate a structured one-page portfolio summary (household snapshot, accounts, top positions, tax-loss candidates, concentration alerts, cash drag, sector allocation).
- **"Generate Meeting Brief" quick-action button** — Surfaces on the empty-chat state as a one-click shortcut to the brief.

### Acceptance test

The full Henderson walkthrough in [ADVISOR_USE_CASE.md](ADVISOR_USE_CASE.md) runs end-to-end entirely inside Asymptote's chat tab, no Claude Desktop required. Live tool-call indicators visible during every query. /brief produces the one-page meeting brief in under 30 seconds.

### Out of scope (stays in the MCP endpoint path)

- MCP client config generators (Claude Desktop JSON, Cursor `.vscode/mcp.json`) stay for power users but stop being the hero path. Moves to v4.7 (Distribution).
- Any feature that duplicates provider UX (artifact rendering, image input, voice, file attachments beyond the existing upload flow).

---

## v4.5 — Meeting capture wedge

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

## v4.6 — Client profile object & advisor analytics

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

## v4.7 — Distribution (deferred from old v4.2)

Getting Asymptote into the places agents already live. **Deferred until at least one advisor firm is using the v4.1–v4.6 stack daily.** Distribution doesn't matter without product-market fit at one customer first.

When it's time:

- **Claude Desktop:** JSON snippet generator already exists (`build_mcp_export_payload`). Polish copy-paste flow + README.
- **Cursor / VS Code:** generator already emits `.vscode/mcp.json` — verify against current Cursor MCP spec.
- **OpenClaw:** minimal skill wrapping the MCP client, dropped into [openclaw/clawhub](https://github.com/openclaw/clawhub).
- **ChatGPT:** once OpenAI's connector API for custom tools stabilizes, add an export profile.
- **Hosted demo collection:** read-only public MCP endpoint pointed at SEC filings / arxiv. Marketing + docs in one.
- **Installer improvements:** single-binary Windows build, Homebrew formula, `pipx install asymptote`.

---

## v4.8 — Ingestion depth (the rest of old v4.3)

PDF table extraction was promoted into v4.1 P0.8 because it's load-bearing for advisor files. The remainder:

### URL / online document support

- Accept URLs alongside file uploads. Store `source_url` + `last_refreshed`. "Refresh from URL" button. Optional periodic refresh per collection. Auth: bearer / basic for private URLs.

### Email ingestion

- `.eml` / `.msg` / Gmail MBOX import. Strip signatures + quoted history at ingest. Thread-level grouping so reply + parent end up adjacent in retrieval. Email is where half the client context lives — pairs naturally with v4.5 meeting capture.

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
- **v4.2 and v4.3 shipped** — enrichment feeds and MCP surface polish.
- **v4.4 shipped** — streaming in-app chat with live tool indicators, `/brief` command, one-click "Generate Meeting Brief" button. The primary demo surface is now self-contained.
- **v4.5 / v4.6** are the advisor-workflow wedge (meeting capture + client profile) that turns this into a product, not a query layer.
- **v4.7 / v4.8** wait until there's daily usage at one firm.
- **v5** is "don't build yet, but if someone asks, this is the shape."
- **Technical debt** is background tax — chip away whenever touching adjacent code.

**Last updated:** 2026-04-17 (v4.4 shipped: SSE streaming chat, /brief slash command, Generate Meeting Brief button, POST /api/collections/{id}/brief endpoint)

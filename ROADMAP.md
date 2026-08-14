# Finn — Roadmap

**Branch `fintech`** · **931 tests passing, 2 skipped** · **Updated 2026-08-14**

Finn is the advisor-facing product. `master` is Asymptote, the domain-neutral tool —
financial modules never go there, and never come out of here.

**This is the single register.** Vision, current state, working notes, forward plan, and
what has already shipped all live here; there is no separate state or handoff file. Start
a session at §0, then §7. `CLAUDE.md` is gitignored on this branch and has drifted into
being `master`'s copy before, so this file is the durable record — design detail beyond
the ledger lives in the linked source and in `git log`.

> **Scope decision, 2026-08-11.** The pilot wedge is **one recurring workflow — the client
> meeting loop** (§1, §7). The three pillars are why Finn is *allowed* near client data;
> the loop is why an advisor *wants* it on a Tuesday. Adjacent categories Finn integrates
> with rather than rebuilds are now enumerated in §3.

---

## 0. Working notes

```bash
python main.py                 # http://localhost:8473  <- PORT in .env on THIS box
cd frontend && npm run build   # REQUIRED before any UI change is visible
pytest                         # 931 passing, 2 skipped
```

> **8473 is a local override, not the project default.** `config.py:28`,
> `.env.example`, every Dockerfile, `nginx.conf`, and the whole README say
> **8000**; only this machine's `.env` sets 8473. Earlier notes here read as if
> 8000 were simply wrong — it is not, and a tester following those notes goes
> looking for the wrong port. `run.sh` / `run.bat` now read `PORT` from `.env`
> and print the real URL instead of hardcoding either value.

**Four gotchas that will waste your time:**

- **`python main.py` runs with `--reload`, and it does not always die.** A session that
  restarts the server a few times leaves several generations alive, all bound to 8473;
  the oldest keeps answering and you debug against stale code for twenty minutes. Check
  with `Get-Process python` before trusting a live response, and note that writing
  scratch files into the repo root retriggers the reloader — write them to a temp dir.

- **The served frontend is committed `static/`**, not `frontend/dist`
  (`vite.config.js` → `outDir: '../static'`). **A `.vue` edit does nothing until
  `npm run build`.** If a UI change "isn't showing up", this is why.
- **Playwright *can* reach the app — use `http://host.docker.internal:8473`, not
  `localhost`.** Earlier sessions recorded "Playwright can't reach localhost" and shipped
  UI work unverified on the strength of it. The cause was mundane: the Playwright MCP server
  runs in a Docker container (`mcp/playwright`, bridge network), so `localhost` is the
  *container*. Docker Desktop injects `host.docker.internal` with no `--add-host` needed.
  Onboarding blocks the first page — clear it with
  `localStorage.setItem('finn_onboarding_completed_at', new Date().toISOString())`.
- **`CLAUDE.md` is gitignored** (`.gitignore:123`) and cannot be recovered from git.
  Rewritten 2026-08-11 after it had silently become `master`'s copy — which told sessions
  on this branch to *delete* the financial modules.

**Don't** run the Docker container and a local `python main.py` against the same `./data`
at once — they diverge.

### The fixtures that back the demo

These are the **answer to "how do I know it's right?"**, not the opening act — see the
demo order in §1.

`tests/fixtures/ingest/messy_unrealized_gl.csv` reproduces the exact failure mode Finn
exists to prevent. Upload it to a **new** collection and ask chat for the total market
value.

| | |
|---|---|
| Real total | **$207,727.45** |
| What SQLite silently returns | **$203.00** |
| Understated by | **$207,524.45** |

Two restricted/annuity rows hold `See contract` / `Priced monthly`, dropping the column to
74% numeric — under the 80% coercion bar — so `Market Value` stays TEXT and `SUM`
truncates every `"27,431.50"` to `27`. The P0.6 guard catches it and tells the model not
to report the number.

> **Fixed 2026-08-12 — the brief was reporting $203.** The guard covered
> `compute_portfolio_metric` and `query_table` but *not* `brief_generator`, which ran its
> own raw `SUM()`. So the one number an advisor reads out loud was the one place the guard
> didn't reach. `generate_meeting_brief` now guards every total and **withholds** a failed
> one (`total_market_value: null`, `total_market_value_reliable: false`) rather than
> showing it with a caveat — $203 next to an asterisk still gets repeated as $203.
> Pinned by [test_brief_coercion_guard.py](tests/test_brief_coercion_guard.py).
>
> **The subtlety worth remembering:** the `__by_symbol` rollup view *launders* the bug. Its
> column is defined as `SUM("Market_Value")`, so the coerced value arrives downstream typed
> REAL and looks clean — sampling the view finds nothing wrong. The guard must sample the
> **base table**. Two tests pin this, including a negative control proving the view-based
> check would have missed it.

> **Still needs a human eye:** does the assistant actually *refuse* the number in chat, or
> report $203 with a footnote? The brief no longer hands it over, and the tool description
> tells the model to read `gaps` — but only a live run proves the model obeys. If it
> reports anyway, the warning wording needs to be louder.

The other half is `euro_semicolon_holdings.csv` (a European export where every US-CSV
assumption is wrong) and `opaque_columns.csv` (headers `Col_1`…`Col_7`, meaning nothing).
Both land with correct totals. Upload `opaque_columns.csv` in front of a prospect and the
Trust Report names the market-value column and shows the arithmetic that proved it.

Clean fixtures (`pershing_unrealized_gl.csv` etc.) deliberately produce **no** warnings —
the guard is precise, not noisy. That is the property a threshold tweak must not break.

---

## 1. The bet

**Two claims, and everything Finn builds follows from them.**

### The bottleneck is the data layer, not the model

Vals AI's [Finance Agent v1.1](https://www.vals.ai/benchmarks/finance_agent) runs 537
questions past frontier models. The best score is **64.37%** — one in three entry-level
analyst tasks failed. The instinctive read is that the models need to get smarter. Look
at the failures on real custodian data and a different picture shows up:

| What's in the file | What happens | What the model reports |
|---|---|---|
| Two annuity rows hold `See contract` instead of a number | Column falls to 74% numeric, under the coercion bar, stays TEXT; `SUM` reads `"27,431.50"` as `27` | **$203.00** instead of **$207,727.45** |
| European export: `1.234,56`, semicolons, cp1252 | Loads as one text column under US-CSV assumptions | Total **0** |
| Header on row 3, two preamble lines above | Every column becomes `Unnamed__N` | "I don't see a market value column" |
| A per-share `Unit Cost` column sits beside `Current Total Cost` | Name matching picks the wrong one as cost basis | Every gain/loss wrong by the share count |

None of these is a reasoning failure. Each produces a confident, well-formatted, wrong
answer, and **the model has no way to know**. A smarter model fails the same way.

Finn is the layer that knows, and says so.

### Regulated users need a boundary they can show, not a policy they can cite

An RIA's compliance officer does not approve "we redact PII." They approve something
they can inspect, re-run, and hand to an examiner. The gap between *having* a privacy
control and *evidencing* one is where every regulated AI deployment actually stalls.

So the boundary has to be an artifact: enumerable paths, named controls, a local audit
log, and a view that shows the real value beside the stand-in that actually crossed.

**Together:** Finn is the trustworthy data layer between a regulated user's private
files and whichever frontier model they already trust.

### The wedge is the client meeting loop

Both claims above say why Finn is *allowed* near client data. Neither says why an advisor
*wants* it on a Tuesday — and an advisor who has never pointed an LLM at a custodian
export has never been burned by silent coercion. Leading a demo with a failure they have
never experienced sells insurance against an unfelt harm, which is the hardest category of
software there is.

The felt cost is one recurring job: **prep the meeting, record it, write it up.** It runs
4–8 times a week, forever. Nobody enjoys assembling a prep packet or writing the note of
record at 9pm. And it happens to consume every pillar — ingest makes the numbers right,
TLH gives prep something worth saying, capture closes it, and the boundary is why it may
touch client data at all. Nothing shipped is orphaned by this framing.

**So the demo order inverts.** Lead with the loop; let the pillars answer the questions it
provokes:

| The prospect asks | Finn answers with |
|---|---|
| "How do I know these numbers are right?" | The **Trust Report** — and the `$203` fixture |
| "What about my client's data?" | The **Boundary Report** — watch the name not leave |

Both land far harder as answers than as opening claims.

**The ROI is arithmetic:** six meetings a week, ~45 min prep, ~20 min notes ≈ **6 hours a
week back.** That is the sentence an advisor repeats to their partner.

---

## 2. What Finn owns

Three pillars. The novelty is not in any single one — it's that nothing else combines
them, and each is built to *produce evidence about itself* rather than best-effort
results.

### Pillar 1 — Ingest that produces evidence, not just rows

Anyone can parse a CSV. What nothing else does is report on its own parse.

Column identity resolves from **three independent signals**
([services/tabular/column_resolver.py](services/tabular/column_resolver.py)):

1. **Name** — token-set matching, brokerage abbreviations expanded, report modifiers
   (`Ending`, `Total`, `(USD)`) treated as noise.
2. **Values** — CUSIP check digit, ISIN Luhn checksum, ticker shape, ISO currency codes.
   Language-independent.
3. **Arithmetic coherence** — `quantity × price = market value`,
   `market value − cost basis = gain/loss`.

**The third is the differentiator.** It names columns whose headers say nothing, in any
language. `opaque_columns.csv` has headers `Col_1`…`Col_7`; five of seven get identified
and the total is right. On the Pershing fixture it *corrected* a name-based match —
picked `Current Total Cost` over the per-share `Unit Cost`, and said so in the report.

Around it: dialect detection ([dialect.py](services/tabular/dialect.py)) that carries
how encoding, delimiter, and decimal convention were each decided and with what
confidence; and a refusal band — below `MIN_ASSIGN_CONFIDENCE` a column gets **no role**,
which is a different state from never having looked.

The **Trust Report** ([ingest_report.py](services/tabular/ingest_report.py) →
[IngestReportPanel.vue](frontend/src/components/IngestReportPanel.vue)) makes all of it
legible to someone who has never heard the word "coercion":

> **Totals from "Market Value" would be wrong**
> 4 rows hold text instead of a number ("See note 3", "Pending valuation", "See
> contract"), so the whole column was stored as text. Adding this column up would read
> "27,431.50" as 27 instead of 27,431.50.

**When they ask "how do I know it's right?"** (not before — see the demo order in §1):
upload a file Finn has never seen, live, and the report names the market-value column and
shows the arithmetic it used to prove it.

### Pillar 2 — A redaction boundary you can watch

Presidio is off the shelf. The differentiated parts are what surrounds it.

**The boundary is enumerated and closed.** There are exactly three paths from a client
document to an external model, each with a named control and its own tests:

| Path | Control | Where |
|---|---|---|
| Tool results | `redact_mcp_response` | [redaction_middleware.py](services/privacy/redaction_middleware.py) |
| Retrieved RAG chunks | `redact_text_for_ai` at payload construction | [chat/context.py](services/chat/context.py) |
| Tabular columns | `column_sanitizer` at **ingest** — PII never reaches disk | [column_sanitizer.py](services/privacy/column_sanitizer.py) |

**Pseudonyms, not tokens.** Default style is `consistent_pseudonym`. Under `[PERSON]`
tokens a transcript naming three people collapses into text where the model can't tell
who agreed to what — the boundary would be buying privacy with wrong answers. Finn does
not make that trade.

**The Boundary Report** ([boundary_report.py](services/privacy/boundary_report.py) →
[RedactionBoundaryPanel.vue](frontend/src/components/RedactionBoundaryPanel.vue)) is the
artifact a CCO actually needs. The audit log answers *what fired*; this answers *what
left, and from where* — grouped by boundary, repeated mentions collapsed into one
substitution with a count, and the local original shown beside the stand-in on request:

| In your record | What the AI saw | Type | Times |
|---|---|---|---|
| john.smith@example.com | `[EMAIL_ADDRESS]` | Email address | 24 |
| Robert Henderson | Marcus Whitfield | Name | 9 |

**When they ask "what about my client's data?"** (not before — see §1): the prospect
watches their client's name leave the payload rather than being told that it does.

### Pillar 3 — Primitives that refuse rather than guess

"Never silently wrong" is enforced, not aspirational.

- **[aggregate_guard.py](services/tabular/aggregate_guard.py)** samples `typeof()` on
  aggregate operands and flags only where SQLite's coercion *disagrees with the human
  reading*: `"1,234.56"` → `1.0` is flagged, `"10"` → `10.0` is not, and neither are
  tickers or an already-`CAST` operand. Precision is the feature — a warning on every
  query is a warning on none.
- **`_sanity_check_sum` / `_sanity_check_breakdown`** ([metrics.py](services/financial/metrics.py))
  flag NULL sums, near-zero sums over many rows, and weights outside 80–120%.
- **Role detection that fails returns a structured `no_role_detected`** and the calling
  model falls back to raw-data tools. It never guesses and presents the guess as fact.
- **Guards never rewrite the result.** Telling the model the number is wrong and handing
  it corrected SQL is honest. Silently substituting a repaired value is a different kind
  of untrustworthy.

---

## 3. What Finn deliberately does not own

**The intelligence.** It lives upstream in Anthropic / OpenAI / whatever the advisor
configured. Finn translates its tool registry into the provider's schema, runs the
standard tool-use loop, and streams tokens. Every hour spent rebuilding chat primitives
is an hour not spent on the three pillars.

Also out of scope, each for a reason:

| Not building | Why |
|---|---|
| Chat-app features (branching, regeneration, voice, artifacts) | Not the product. The provider SDKs own conversation mechanics |
| Anthropic Files API | Bypasses the PII boundary — the one thing Finn cannot compromise |
| Code-execution tool | Undoes the deterministic-primitives mandate |
| Anthropic Agent SDK migration | Finn treats OpenAI / Ollama / Grok / Google as first-class; the SDK is Anthropic-only |
| Anthropic-hosted memory | Conflicts with local-first. Expertise packs + collection guides cover the job locally |
| `web_search` | yfinance covers the data, and routing queries through a provider leaks advisor intent |

### Adjacent categories Finn integrates with rather than rebuilds

A prospect wish-list (2026-08-11) asked for twelve capabilities spanning **four mature
software categories**. Recording the triage here so it does not have to be re-litigated —
and so nobody promises these in a room:

| Asked for | Category / incumbents | Why not |
|---|---|---|
| **Open accounts**, transfers, RMDs, distributions | Custodian operations — Schwab Advisor API, Fidelity Integration Xchange, Pershing | **A partner program, not a build.** Application, security review, E&O minimums, and in places a requirement that you already serve firms on-platform. No amount of effort compresses it; the door opens from the inside, via a customer's own custodian rep |
| **Financial plan based on risk**; Monte Carlo | Financial planning — eMoney, MoneyGuidePro, RightCapital | A plan is a document a client relies on and an examiner reads; a wrong projection lands on the advisor's fiduciary duty. The math is weeks. The actuarial and compliance scaffolding to *sign* it is not |
| **Lead generation**, referral generation | CRM + marketing — Redtail, Wealthbox, Snappy Kraken | Needs purchased external data and outbound contact — architecturally opposed to local-first. A different product with an opposite privacy story |
| **Stocks by parameter** (screening) | Screening — needs Polygon / Tiingo / OpenBB | yfinance cannot screen. Revisit only if a customer asks twice |

The single-pane-of-glass is the **most-attempted idea in advisor tech**: Envestnet
assembled that footprint by acquisition over a decade; Advyzon has been building toward it
since 2012 with a real team. Breadth is where a solo build dies — twelve mediocre features
losing to twelve specialists.

**The unprecedented thing Finn already has is the boundary, not the breadth.** Nobody has
shipped a way to put a frontier model on real client data with an inspectable, re-runnable
artifact a CCO can hand an examiner. Claude for Excel cannot. Orion's AI runs on Orion's
cloud under Orion's terms. Depth on that, plus integration into the stack the firm already
owns, is the defensible bet.

**How to answer the wish-list without saying no** — sequence it, and make the sequence the
pitch. Phase 1 (this quarter): the meeting loop and the boundary. Phase 2: planning and
cash flows, with a planning specialist reviewing the projection logic. Phase 3: custodian
integration — *and I will need your firm to introduce me to your custodian rep.* That last
clause converts the hardest item from a promise you will break into a reason to partner.

---

## 4. Why this is hard to copy

The dismissive read is "it's CSV parsing plus Presidio." The actual work is in the
**constraints**, and every constraint below is a confidently-wrong answer someone had to
catch first. Each is now a regression test:

- The arithmetic permutation search may only *fill in* or *upgrade*, never contradict a
  column already resolved at `likely` or better. Unconstrained, it relabels Pershing's
  `Unit Cost` as the price — because `quantity × unit cost = total cost` balances
  perfectly.
- Columns must **vary**. Three columns holding 1, 2, 3 on every row satisfy `a − b = c`
  and mean nothing.
- Checksum-backed identifiers need **cardinality**. Fidelity's account number
  `X12345678` repeats on every row and *is* a valid CUSIP.
- Profile roles are human assertions: never demoted, never relabelled by arithmetic.
  Corroboration is not a new provenance.
- Non-profile duplicates of a single-valued role *are* demoted, so a sum over
  `cost_basis` cannot double-count.
- Redaction must not mutate `filtered_results` — those same objects stream to the
  advisor's sources panel, where they *are* authorized to see real text. Redacting in
  place would silently blank the advisor's own evidence.
- Boundary Report dedupe keys on `(entity_type, original, replacement)`. Keying on the
  replacement alone merges three clients into one row under `[PERSON]`.
- The Trust Report's empty-column threshold is 90%, not 50% — a flat Pershing file
  blanks `Symbol` on lot rows, and that is the format working correctly. All five vendor
  fixtures must grade `clean`, or the badge means nothing.

That list only comes from running real files and being wrong in public. It compounds.

**How we'll know we're right:** the eval suite (§8) publishes **Finn lift** — agent
accuracy with Finn's tools versus the same model without them. Anthropic publishes
64.37%; the number Finn should be able to publish is the delta.

---

## 5. Who it's for

**Now:** solo RIAs and small advisor shops (1–5 advisors), plus mid-market firms in
evaluation.

Anthropic's [Financial Services launch](https://www.anthropic.com/news/finance-agents)
went to institutional buy-side and sell-side — Citadel, Carlyle, BNY — with
enterprise-tier data partners. **Solo RIAs are not in that picture.** The
retail-custodian-export wedge is open territory. The real competitive surface is *Claude
for Excel*, and it cannot parse a messy custodian export, has no household model, and
has no redaction boundary.

**Generalizes to:** any regulated user with messy tabular exports and PII/CUI that
cannot cross to an external model — federal contractors (CUI), healthcare, legal. The
architecture is domain-neutral; the vendor profiles and advisor framing are the
financial skin over it.

### "It replaces your assistant" — do not pitch this

Floated 2026-08-11. It does not survive the room, and the reason is architectural.

Of Finn's 26 MCP tools, **25 are read-only** — the only writer is `set_redaction_policy`,
which changes Finn's own config. Finn reads, analyzes, and refuses to guess, deliberately.
An assistant's job is overwhelmingly a *write* job: submit the form, send the email, book
the meeting, update the CRM, call the custodian.

| Assistant work Finn does | Assistant work Finn cannot touch |
|---|---|
| Meeting prep packet | Account servicing — ACATS, RMDs, distributions, beneficiary changes |
| Note of record + action items | Keying that note into Redtail / Wealthbox |
| "What did we tell her last time?" | Scheduling, confirmations, rescheduling |
| Ad-hoc portfolio questions | Inbound client calls |
| TLH and household wash-sale checks | Placing the trades; chasing the CPA, attorney, custodian |

That is roughly **20–30% of an assistant's hours**, and it is the analytical slice, not the
operational or relational one. Three ways the framing backfires: it is falsifiable in
twenty minutes ("can it process an RMD?"); the assistant is frequently the person
evaluating or adopting the software; and at many RIAs the CSA is licensed and holds the
client relationship.

**The true version:** *"Don't hire your first one yet"* — for a solo advisor doing their
own prep at 9pm, Finn replaces unpaid evening hours, not a person. Or: *"your assistant
covers three advisors instead of two."*

> A live alternative strategy says the real money is the nuclear/utility vertical
> instead. Deliberately **not** the current plan — finance is. Revisit only on a
> deliberate pivot.

---

## 6. Where it stands

| Capability | Status |
|---|---|
| Ingest fidelity (P0.1–P0.8) | ✅ Shipped — header detection, numeric coercion, lot rollup, vendor profiles, LLM role inference, sanity guards, regression fixtures, PDF tables |
| Universal ingest (unfamiliar files) | ✅ Shipped — dialect detection + three-signal column resolution |
| Trust Report | ✅ Shipped |
| PII redaction (P0.0), all three boundaries | ✅ Shipped |
| Boundary Report | ✅ Shipped, browser-verified |
| Compliance one-pager | ✅ Shipped — [COMPLIANCE.md](COMPLIANCE.md) |
| In-app chat + tool-use loop (v4.4, v4.4.1) | ✅ Shipped |
| Anthropic alignment (v4.4.4) | 🟡 Caching / citations / extended thinking ✅; compaction open |
| Provider tiers (v4.4.5) | 🟡 Default / Quality / Sovereignty ✅; Bedrock + tier-aware onboarding open |
| Meeting capture → structured notes (v4.5) | ✅ Shipped; `prep_for_meeting` composite open |
| Market data enrichment (v4.2) | ✅ Shipped — yfinance behind a provider protocol |
| MCP surface (v4.3) | ✅ Shipped |
| Advisor desktop UX (v4.4.3) | 🟡 R1–R10 ✅; §13 manual signoff open |
| TLH skill (v4.6.1) | ✅ Shipped — household wash-sale check is the moat |
| Dashboard (v4.9) | 🟡 Overview tab + brief charts ✅; chat chart adapters open |
| Outbound digests (v4.10) | 🟡 Friday digest + morning brief ✅; calendar OAuth open |
| **`prep_for_meeting`** | ✅ **Shipped 2026-08-12** — composite, no LLM, `gaps` first-class |
| **Client profile / IPS (v4.6)** | ✅ **Shipped 2026-08-12** — typed store, drift engine, form. Unblocks Rebalance |
| **Meeting-loop tools in chat** | ✅ **Fixed 2026-08-12** — all six reachable; the kind filter had been dropping the v4.6 keystones |
| **Untrusted-content guardrail** | ✅ **Shipped 2026-08-12** — every system prompt, 13 tests |
| **Tester handoff** | 🟡 [TESTING.md](TESTING.md) ✅; feedback channel (`RESEND_API_KEY`) and packaging open |
| **Auth / hosted access** | 🟡 Access JWT verification + 401-on-missing-identity ✅, [DEPLOYMENT.md](DEPLOYMENT.md) ✅; **not deployed**, runbook unverified live |
| **Deployable container** | 🟡 Dockerfile `$PORT` + spaCy model fixed, `/health` reports redaction enforcement ✅, [HOSTING.md](HOSTING.md) ✅; **never built or run** |
| Monte Carlo, screening, planning, lead gen, account opening | ❌ Not v1 — see §3 and §8 |
| Eval suite (v4.6.2) | ❌ Open |

Full detail per item: [Appendix A](#appendix-a--shipped-ledger).

---

## 7. Now — close the loop

**The pilot demo is one workflow, end to end: prep a client meeting, record it, write it
up.** Fifteen minutes, on the prospect's own file. Work that does not serve that loop is
not demo-blocking, however nice it would be.

### The loop — closed 2026-08-12

| Step | Status |
|---|---|
| Their export lands clean | ✅ dialect detection, three-signal resolution, Trust Report, 5 vendor profiles, PDF tables |
| **Prep the meeting** | ✅ **`prep_for_meeting`** — one call, nine sections, no LLM |
| Something worth *saying* in prep | ✅ TLH + client profile — drift, concentration, exclusions, cash policy |
| Record it | ✅ v4.5 |
| Notes + action items out | ✅ v4.5 |
| Nothing leaks | ✅ three boundaries + Boundary Report |

**Both §7 builds shipped.** [services/meeting_prep.py](services/meeting_prep.py) composes
meeting history, portfolio, IPS policy, TLH, and corporate events into a priority-ordered
agenda plus a `gaps` list. [services/financial/client_profile.py](services/financial/client_profile.py)
stores the policy; [services/financial/ips_drift.py](services/financial/ips_drift.py) turns
it into arithmetic. Surfaced as MCP tools, chat tools, HTTP routes, and two Vue components.

Three properties are load-bearing and should survive future edits:

- **No LLM anywhere in prep.** Every line is computed, so the page costs nothing, cannot
  fabricate, and renders identically twice — which is what makes it safe to run live in
  front of a prospect.
- **`gaps` is a section, not a footnote.** Anything prep could not determine is named in
  the advisor's language with a remedy. Sections degrade one at a time: a dead market-data
  provider removes one block and records a gap; it never fails the page.
- **Label matching is auditable.** An IPS target of "Fixed Income" matching an export's
  "Bonds" is a guess, so every match carries `matched_by: exact|alias`, unmatched targets
  are reported at 0% rather than dropped, and drift goes `authoritative: false` when
  asset-class coverage is under 90%.

### Verified live, end to end (2026-08-12)

Fresh collection → upload `messy_unrealized_gl.csv` → save a profile → `POST /prep`. The
page correctly **withheld** the market-value total, named the coercion as a portfolio gap,
and then cascaded honestly: allocation drift and the concentration check both reported
*why* they could not run rather than returning "no issues." The prohibited-holding match
(`XOM`) sorted to the top as the one item that cannot wait, ahead of wash-sale risk and
harvestable losses. That cascade is the demo.

Then stop building features and go sell it.

### Demo-blocking, but not features

- **`FALLBACK_API_KEY` is unset locally**, so the keyless path cannot be exercised — and
  both live verifications below need a working chat turn. Note this file previously called
  the setting `managed_ollama_cloud_api_key`; that name survives only in a docstring. The
  real one is `fallback_api_key` / `FALLBACK_API_KEY` ([config.py:71](config.py#L71)),
  consumed by `_build_ai_provider_from_headers` ([main.py:4646](main.py#L4646)).
- **Does the model actually refuse the `$203`?** Unit tests prove the guard fires; only a
  live run proves the model obeys it. If it reports the number with a footnote instead of
  refusing, the warning wording needs to be louder.
- **Exercise the Boundary Report against a live chat turn** — verified against historical
  `search_collection` events, not a fresh `chat_context` one.
- **Ask Ollama for a named subprocessor list.** Their policy says "model inference
  providers" without naming them, so the no-retention commitment rests on an unpublished
  flow-down — the one question a diligence reviewer will find in COMPLIANCE.md §2 Tier 1.
  An email, not code.
- **`RESEND_API_KEY` is unset**, so the in-app "Report issue" button returns 503
  ([config.py:146](config.py#L146)). That is the tester feedback channel — it should be
  live before anyone outside the building is handed a build.
  [TESTING.md](TESTING.md) §6 currently routes testers to email instead.
- ~~**`run.bat` / `run.sh` print the wrong port.**~~ **Done 2026-08-12** — both scripts
  hardcoded "Starting server on port 8000" at line 44. They now resolve `PORT` from `.env`
  (falling back to the config default of 8000) and print the full URL.
- ~~**Rename Collections → Clients.**~~ **Done 2026-08-12** — 33 user-visible strings in
  [App.vue](frontend/src/App.vue): heading, card grid, search/sort labels, and the create /
  edit / delete / share modals. Identifiers (`collection_id`, `activeTab === 'collections'`,
  the Pinia store, every API path) are untouched by design — this was a labelling change,
  not a rename.
- **AI-by-default onboarding** already suppresses the key prompt when the managed provider
  is live ([WelcomeOnboarding.vue:66](frontend/src/components/WelcomeOnboarding.vue#L66)) —
  verify end to end once the fallback key is set.

### Handing a build to testers

[TESTING.md](TESTING.md) is the tester-facing doc — 15-minute setup, a 30-minute
guided pass over the loop, the two proof fixtures, a known-issues table, and a plain
statement of where data lives and what crosses the boundary. It leads with what works
**without** an API key (ingest, Trust Report, prep, TLH, Boundary Report), because that
is most of Finn and none of it is currently exercisable on a fresh install.

It tells testers that a `$203` answer on `messy_unrealized_gl.csv` is the single most
valuable bug they can report. That is the same question §0 leaves open for a live run —
so the first tester who reaches it answers it either way.

**Hosting: [HOSTING.md](HOSTING.md) (2026-08-14)** is the entry point — picks between
running on your own machine and Railway, rules Firebase out with reasons, and carries the
verification checks. [DEPLOYMENT.md](DEPLOYMENT.md) remains the deep-dive for the
own-machine path.

> **The container was never deployable, and it failed in the worst direction.** The
> Dockerfile hardcoded `--port 8000`, which every PaaS (Railway, Render, Fly, Cloud Run)
> routes around — a 502 at the edge with a healthy-looking container. Worse, it never ran
> `python -m spacy download en_core_web_lg`; `requirements.txt:55` mentions it only in a
> comment. Presidio loads that model at runtime, so **an image built from the old
> Dockerfile would have started cleanly and then raised on the first redaction** — and if
> Presidio itself is ever missing, `redact_text_for_ai` returns the input unchanged
> ([redaction_middleware.py:187](services/privacy/redaction_middleware.py#L187)), sending
> client PII to the model while the UI still reports redaction as on. Both are fixed in
> the Dockerfile; `HOST` is now overridable for Railway's IPv6-only legacy environments.
>
> **`/health` now reports the boundary's real state** — `enabled`, `engine_available`,
> `model_ready`, and `enforcing`. `"enforcing": true` is the single field a deploy check
> should assert, and HOSTING.md §4 makes it a required step. 5 tests, including both
> failure modes: [test_redaction_health.py](tests/test_redaction_health.py).

**Auth: DEPLOYMENT.md (2026-08-12).** Cloudflare Tunnel + Access,
chosen over building a PIN gate because it is less work *and* stronger. Not deployed —
the runbook is written and the app side is done.

> **What the investigation turned up, and it is the important part.** Finn had **no
> authentication of any kind**, and [middleware/user_context.py](middleware/user_context.py)
> made that worse than it looks: in multi-user mode, a request with no identity header
> silently resolved to `default_user_id` — the account that owns every collection on a
> machine upgraded from single-user mode. The bypass and the jackpot were the same
> account. It also trusted `Cf-Access-Authenticated-User-Email` in plaintext, which
> Cloudflare explicitly warns against: *"Validation of the header alone is not sufficient
> — the JWT and signature must be confirmed to avoid identity spoofing."*
>
> Now: [access_auth.py](services/access_auth.py) verifies the `Cf-Access-Jwt-Assertion`
> signature against the team JWKS with `aud` and `iss` checked, and a missing or bad
> identity is a **401** rather than a fallback. Plain-header trust survives only behind an
> explicit, default-off `TRUST_PROXY_USER_HEADER`. 20 tests, signing real RS256 tokens —
> including `alg: none`, wrong-key, wrong-`aud`, wrong-`iss`, expired, and service-token
> rejection. See [test_access_auth.py](tests/test_access_auth.py).
>
> Two smaller holes closed alongside: `/api/users` ("admin view", lists every user's
> email) had **no auth dependency at all**, and CORS was `allow_origins=["*"]` with
> credentials — now `CORS_ALLOW_ORIGINS`, with a warning logged if left wide open in
> multi-user mode.

**Turning multi-user on reassigns nothing by itself.** All 3 existing collections are
owned by `default`; flipping the switch means arriving as an email address and seeing an
empty app. [scripts/reassign_owner.py](scripts/reassign_owner.py) migrates ownership
(dry-run by default, backs up before writing). **Not applied** — it is a data change and
belongs to whoever flips the switch.

Still open before a build actually goes out:

- **`RESEND_API_KEY`** — the in-app "Report issue" button 503s without it, so the
  feedback channel is email until it is set.
- **Packaging.** TESTING.md assumes a git clone and a Python toolchain. Fine for a
  technical tester, wrong for an advisor. Single-binary / `pipx` is §9 (v4.7) and does
  not need to block a first technical round — and hosting it centrally per
  [DEPLOYMENT.md](DEPLOYMENT.md) sidesteps packaging entirely for a first round.
- **Actually deploying it.** The runbook is untested against a live tunnel. Check 3 in
  §6 of that doc — proving the origin is not reachable directly — is the one that
  matters and the one most likely to fail first time (Finn binds `0.0.0.0` by default;
  hosting wants `HOST=127.0.0.1`).
- **A seed collection.** First launch is empty, so a tester's first impression depends
  entirely on their own file landing well. A pre-ingested demo client would derisk that.

### Deferred out of v1 — deliberately

Real, but they do not serve the loop, and each has either a strong incumbent or a quiet
failure mode a prospect is unlikely to hit live:

- **Monte Carlo** — planning-adjacent, and eMoney / MoneyGuidePro are strongest exactly
  there. Moved to §8.
- **Ingest structure edge cases** — two-row / merged Excel headers, two tables stacked on
  one sheet, wide-format reports with periods as columns, and `_detect_header_row`
  returning `0` both when the header genuinely is row 1 and when it gave up
  ([document_extractor.py:112-120](services/document_extractor.py#L112-L120)), so the Trust
  Report cannot distinguish "confirmed" from "assumed." Moved to §8 — recoverable live by
  naming it as a known gap.

---

## 8. Next — after the loop closes

Ordered. Each is small enough to land in days, not weeks. **Nothing here starts before the
two §7 items ship** — the loop is the product; this is what deepens it.

1. ~~**Untrusted-content guardrail.**~~ **Shipped 2026-08-12.** `_UNTRUSTED_CONTENT_RULE`
   in [chat/context.py](services/chat/context.py) is appended to every assembled system
   prompt, on every collection kind. Pinned by
   [test_untrusted_content_guardrail.py](tests/test_untrusted_content_guardrail.py) (13 tests).

   Three things worth keeping if this is ever edited:

   - **It goes last**, nearest the content it governs, and a test asserts that. A future
     section appended after it should be a deliberate choice, not a merge artifact.
   - **ADVISOR EXPERTISE is explicitly exempt.** The same prompt tells the model to treat
     expertise packs as authoritative — they are advisor-authored config, not ingested
     third-party text. Without the carve-out the two instructions contradict each other
     and the model picks.
   - **It names the response, not just the prohibition** — flag it to the advisor, quote
     it, name the document, then carry on. "Don't obey it" alone lets the model drop the
     finding silently, which is also what would make it invisible in a demo.

   This item's original text said to add the same block to
   [brief_generator.py](services/brief_generator.py). **Stale** — the brief generator has
   no LLM prompt at all; it is fully deterministic. Nothing to guard there.

   Defense in depth already existed on either side and neither was the gap:
   [prompt_injection_detector.py](services/prompt_injection_detector.py) scans pages at
   ingest via `document_extractor`, and the v4.5 meeting-notes extraction prompt carries
   its own guard. The chat system prompt was the hole.

2. **Brief format upgrade** — directly deepens the loop. The shipped `/brief` is
   prose-heavy; a table-first layout scans in 30 seconds before a review. Four sections
   above the existing ones: performance attribution (top 3 contributors / detractors,
   QTD / YTD / 1Y), allocation drift (current / target / drift / action), a 5-section
   agenda scaffold, and a proactive recommendations footer that cites the check behind
   each item. Degrades with a labelled empty state when price history or IPS targets are
   absent.

3. **Ingest structure edge cases** *(deferred out of v1, §7)* — two-row / merged Excel
   headers, two tables stacked on one sheet, wide-format period-as-column reports, and a
   confidence signal for `_detect_header_row` so the Trust Report can say "confirmed"
   versus "assumed" instead of returning `0` for both. Each is common in real statements
   and each fails quietly today. Promote any one of these the first time a prospect's own
   file hits it.

4. **Monte Carlo (`run_monte_carlo`)** *(moved down from §9)* — percentile bands,
   read-only, deterministic seed. Well-understood and buildable in about a week, but it is
   planning-adjacent and the incumbents are strongest there, so it earns its place only
   once the loop is landing. Also unblocks the eval suite's forecasting category.

5. **Agent-task eval suite (v4.6.2)** — this is what turns the thesis in §1 into a
   number. Four categories mapped onto advisor questions (simple retrieval quantitative /
   qualitative, aggregation, forecasting), fixtures in `tests/fixtures/eval/` pairing data
   with golden answers, harness on `FakeAIProvider` for speed plus an optional real-key CI
   job. Record pass/fail, tool calls, tokens, wall-clock, and cost per question.
   **Baseline mode strips the tool registry** — the delta is the publishable Finn lift.
   The forecasting category gates on #4 and can be skipped in v1.

6. **Portfolio Rebalance skill** — `drift_analysis`, `suggest_trades` (tax-aware
   ordering: IRA/Roth first, harvest while rebalancing, cross-check `check_wash_sale`),
   `asset_location_review`. Composes with TLH so the advisor sees one combined ticket.
   Gated on the client profile object (§7).

7. **Chat tool-result chart adapters (v4.9)** — when `tool_end` carries a chartable
   shape, render a chart card instead of a table. `{points}` → line, `{groups}` → donut /
   bar, `{rows, columns}` with one numeric column → horizontal bar. Shape-sniffing is
   enough for v1; `chart_hint` on the payload is optional later.

8. **Conversation compaction (v4.4.4)** — long client-review chats grow past the window.
   Anthropic's automatic compaction is the path of least resistance; manual
   "summarize prior N turns" for the rest.

**Also open, smaller:** v4.4.3 §13 manual e2e signoff (at-machine: mic permission, print
dialog); tier-aware onboarding copy (v4.4.5); promote `/brief` from modal to route;
interleaved thinking; `meetings` as a first-class doctype; frontend "Draft Meeting Notes"
button; re-cast README + ADVISOR_USE_CASE now the dashboard is the daily driver.

---

## 9. Later — after daily use at one firm

Distribution and depth both wait for product-market fit at a single customer.

**Distribution (v4.7).** Claude Desktop JSON generator (exists, needs polish), Cursor /
VS Code `.vscode/mcp.json` verification, a hosted read-only demo collection,
single-binary Windows build / Homebrew / `pipx install finn`.

**Ingestion depth (v4.8).** URL ingestion with refresh; email (`.eml` / `.msg` / MBOX)
with signature and quoted-history stripping and thread-level grouping — half of client
context lives in email, and it pairs naturally with meeting capture; semantic /
structure-aware chunking.

**Analytics depth (v4.6).** `compare_holdings_to_profile` and additional metrics — max
drawdown, factor exposure, YTM rollup, effective duration. (`run_monte_carlo` moved up to
§8 #4 — a prospect asked for it by name.)

**Outbound (v4.10 Phase 2).** Google Calendar + MS Graph OAuth on top of the shipped
morning brief, adding today's agenda and per-meeting prep; auto-draft Note of Record from
Zoom/Teams recordings; forwarding inbox at `brief@finn.cyberlion.dev`. Whether the inbox
becomes a third primary surface depends on whether the shipped digests earn their keep
with the first advisors.

---

## 10. Speculative

Not funded; same direction of travel.

- **Two-way custodian / CRM integration** — the big one. Pick the custodian the first
  firm uses; read-only for 6+ months before any write-back. Candidates: Schwab Advisor
  API, Wealthbox, Redtail.
- **Market data provider swap** — `MarketDataProvider` protocol already abstracts
  yfinance. Swap to OpenBB / Polygon / Tiingo on persistent 429s, a schema break lasting
  >24h, or instruments yfinance can't classify (munis, pre-IPO, options chains).
- **AWS Bedrock provider (v4.4.5 Enterprise tier)** — FedRAMP High, HIPAA-eligible, BAA,
  IL5. **Only when a paying enterprise customer asks.** Building it pre-pull is the wrong
  order.
- Stock screener; `rewrite_query` before retrieval; multi-hop retrieval; multi-user mode
  (or delete the half-built config); hosted multi-tenant (SOC 2 is months — don't start
  without a paying customer); enterprise SSO / rate limiting / retention; GPU embeddings,
  Postgres + pgvector, Redis.

---

## 11. Technical debt

1. **`main.py` is ~6,200 lines and 112 routes** — the single largest complexity
   liability. `master` already split this into an `api/` package. Big, mechanical, risky;
   not before a demo, but it is the thing that will slow everything down after one.
   **The split has now started rather than being deferred whole.** v4.6's four routes
   landed in [api/](api/) instead of `main.py`, with the shared request dependencies
   (`get_indexer`, `require_collection_access`) moved to [api/deps.py](api/deps.py) so
   router modules can use them without importing `main` — which would be circular.
   `main.py` re-imports both from there, so existing tests that `monkeypatch.setattr(main,
   "get_indexer", ...)` still work unchanged. Migrate the remaining routes in batches by
   domain; the landing zone exists now, which was the hard part.
2. **The `__by_symbol` rollup view can hide a coercion bug.** It defines its columns as
   `SUM("<col>")`, so a TEXT column arrives downstream typed REAL and already truncated.
   Anything that validates a column's storage type must sample the **base table**, not the
   view — this cost real debugging time on 2026-08-12 and will again. See
   `_guard` in [brief_generator.py](services/brief_generator.py) and the negative-control
   test in [test_brief_coercion_guard.py](tests/test_brief_coercion_guard.py). Worth
   auditing whether any other consumer reads the view and trusts its types.
3. ~~**The meeting-loop tools are not all in the chat surface.**~~ **Fixed 2026-08-12 —
   and it was worse than this entry described.** All four MCP-only tools
   (`generate_meeting_brief`, `find_tax_loss_candidates`, `get_meeting_notes`,
   `list_action_items`) are now registered in [agent_tools.py](services/agent_tools.py),
   dispatched in [structured_chat.py](services/structured_chat.py), and described in the
   ReAct prompt.

   > **The buried bug:** `prep_for_meeting` and `get_client_profile` were registered and
   > dispatched in v4.6 — and were still **unreachable from chat on Anthropic and OpenAI**,
   > which is the demo path. [chat/context.py](services/chat/context.py) always sets
   > `allowed_tool_names = tools_for_kind(kind)`, and
   > [collection_context.py](services/collection_context.py) never added the v4.6 tools to
   > any kind's set, so `filter_tool_specs` silently dropped them from every turn. The
   > module header says "keep these in sync when a new tool ships"; a comment in
   > `tools_for_kind` even described the follow-up. Nothing enforced it. Typing "prep me
   > for the meeting with X" into chat could not reach the §7 keystone.
   >
   > Registration in three places and reachability in a fourth is the shape of the bug.
   > Two tests now close it: every registered tool must be advertised by some kind, and
   > every advertised tool must have a dispatcher —
   > [test_collection_context.py](tests/test_collection_context.py).
   >
   > Kind assignment: advisory composites (`prep_for_meeting`, `get_client_profile`,
   > `find_tax_loss_candidates`, `generate_meeting_brief`) ride with the financial tools
   > since they read holdings; `get_meeting_notes` / `list_action_items` attach to
   > `meetings`; `mixed` gets both. `prep_for_meeting` is the one exception — it stays on
   > the menu for a transcripts-only collection because it degrades to the meeting half
   > and records the missing portfolio as a gap.

   Still ReAct-only, never registered natively: `enrich_holdings` and
   `get_corporate_events`. They predate the registry and are reachable on providers
   without native tool use. Low priority, but the same class of gap.
4. **Structured error codes for MCP.** Errors raise `ValueError` with prose. A taxonomy
   (`collection_not_found`, `sql_invalid`, `ingestion_failed_no_header`) lets hosts retry
   intelligently instead of parsing strings.
5. **Type completeness.** [mcp_server.py](services/mcp_server.py) is well-typed; extend
   to [upload_service.py](services/upload_service.py) and
   [indexing/indexer.py](services/indexing/indexer.py).
6. **Centralized settings validation.** [config.py](config.py) has grown organically —
   one consolidation-and-document pass.
7. **Structured JSON logging** behind a setting; current logs are grep-friendly, not
   machine-friendly.
8. **`_FORBIDDEN_KEYWORDS` audit.** Re-check against SQLite docs — block DDL/DML/admin
   only, not legitimate string / math / window / CTE / aggregate functions.
9. **Redaction fails open when Presidio is absent.**
   [redaction_middleware.py:187](services/privacy/redaction_middleware.py#L187) and
   [redaction_engine.py:165](services/privacy/redaction_engine.py#L165) both return the
   input text unchanged when `redaction_engine.available` is False. For the product whose
   whole claim is the boundary, the safe default is to **refuse** rather than pass PII
   through — a startup check that halts, or a hard error at the call site. `/health`
   now makes the state visible, which closes the "you can't tell" half of the problem,
   but not the "it silently did the wrong thing" half. The missing-spaCy-model case
   already raises loudly; only the missing-Presidio case is fail-open.
10. **Docling secondary pass** for low-confidence PDF tables (P0.8 follow-up). Lands when
    a customer hits a real scanned-statement case.
11. **Long-tail vendor profiles** (P0.4) — Raymond James, LPL, Edward Jones, Morgan
    Stanley. Profiles are plain YAML; add on customer pull, never speculatively.

---

## 12. Decisions waiting on you

Not blocked on engineering — blocked on a call.

- ~~The `default` collection is 16 SAPHIRE/NRC documents~~ — **resolved.** Verified
  2026-08-11: `data/collections/default/documents/` holds 0 files. The purge happened; no
  decision needed. Left here so a future session does not re-investigate.
- **Two orphan collection dirs** (`0675447f`, `630c621e`, 7.9 MB) hold Pershing/NetX360
  exports from deleted collections. Possibly useful as fixtures; left alone.
- **Documents ingested before the Trust Report shipped have no `ingest_json`**, so their
  reports omit the vendor-match and header-offset lines (`recognized_as: null`). Re-upload
  before demoing.
- ~~**`run.bat` / `run.sh` may still assume port 8000**~~ — verified and fixed 2026-08-12;
  see §7. The finding was the inverse of the suspicion: 8000 is the correct project
  default, and 8473 is this box's override.
- ~~**A config row holds an API key that doesn't look Anthropic-shaped.**~~ **Resolved
  2026-08-12 — the diagnosis here was wrong.** The `id.secret` (Zhipu/GLM-shaped) value is
  `vision_ocr_api_key`, not a chat credential, and `vision_ocr_provider` is `none`, so it
  is inert. It cannot be why chat errors.

  What the inspection actually found: `ai_preferences` and `user_api_keys` are both
  **empty**, and `FALLBACK_API_KEY` is unset — so there is no chat credential anywhere.
  Chat cannot work on this machine until a key is supplied, which is the same wall a
  tester hits on first launch ([TESTING.md](TESTING.md) §2). `data/` is gitignored and
  `data/app.db` is untracked, so the stray key is not in git — but it is a live-looking
  credential that should be cleared before this machine's `data/` is ever handed on.

---

## Appendix A — shipped ledger

One line per item. Identifiers are load-bearing — code comments and
[COMPLIANCE.md](COMPLIANCE.md) cite them. Design detail lives in the linked source and in
`git log`.

### v4.1 — Ingestion fidelity (P0)

| ID | Item | Where |
|---|---|---|
| P0.0 | PII redaction — Presidio engine, middleware, per-collection policy, SQLite audit log, 4 MCP tools, 3 HTTP endpoints. Extended 2026-08-11 to the RAG path, which had never been covered. Default style `consistent_pseudonym` (stable via SHA-256 — the previous builtin `hash()` was salted per process, so "consistent" pseudonyms silently changed on restart). Measured at ~24 ms/chunk, ~240 ms/turn — not worth optimizing | [services/privacy/](services/privacy/), [test_pii_redaction.py](tests/test_pii_redaction.py), [test_chat_context_redaction.py](tests/test_chat_context_redaction.py) |
| — | **Boundary Report** — audit log rendered as *what left and from where*; grouped by boundary, repeat mentions collapsed, originals gated behind a server-side `reveal` param (the test asserts against the serialized body, so a nested structure can't smuggle them back in). Browser-verified | [boundary_report.py](services/privacy/boundary_report.py), [test_boundary_report.py](tests/test_boundary_report.py) |
| P0.1 | Smart header detection — scans 30 rows, drops preamble, surfaces it as metadata; wired into all 5 tabular read sites | [document_extractor.py](services/document_extractor.py) |
| P0.2 | Numeric coercion — `$`, commas, accounting parens, K/M/B; 80% per-column threshold; `__raw` sibling preserves the original | [type_hints.py](services/financial/type_hints.py), [inference.py](services/tabular/inference.py) |
| P0.3 | Lot rollup — auto-created `<table>__by_symbol` VIEW; `compute_portfolio_metric` uses it when `group_by_symbol` | [holdings_store.py](services/financial/holdings_store.py) |
| P0.4 | Vendor profiles — YAML framework + Pershing / Schwab / Fidelity / Vanguard / NetX360 (with a hierarchical preprocessor that flattens multi-account exports) | [services/ingest_profiles/](services/ingest_profiles/) |
| P0.5 | LLM role inference — narrow, confidence-gated, fail-closed; sample values redacted before transmission; `role_source` provenance surfaced per column | [llm_role_inference.py](services/llm_role_inference.py), [test_role_provenance.py](tests/test_role_provenance.py) |
| P0.6 | Numeric sanity guards — metric path (`_sanity_check_sum`/`_breakdown`) + SQL path (`typeof()` sampling, flags only where coercion disagrees with the human reading). Guards append, never suppress or rewrite | [aggregate_guard.py](services/tabular/aggregate_guard.py), [test_aggregate_guard.py](tests/test_aggregate_guard.py) |
| P0.6b | **Guard extended to the meeting brief (2026-08-12)** — `generate_meeting_brief` ran its own raw `SUM()` and was reporting the fixture's coerced $203 total. It now guards every total against the **base table** (the `__by_symbol` view launders the bug) and **withholds** a failed one rather than caveating it | [brief_generator.py](services/brief_generator.py), [test_brief_coercion_guard.py](tests/test_brief_coercion_guard.py) |
| P0.7 | Regression suite — 5 vendor fixtures + frozen snapshots (columns, types, roles, provenance, profile, canonical aggregate); drift fails loudly, `UPDATE_SNAPSHOTS=1` reseeds | [tests/fixtures/](tests/fixtures/), [_snapshot_helper.py](tests/_snapshot_helper.py) |
| P0.8 | PDF table extraction — pdfplumber per page through the same header/coercion/profile pipeline; tables *and* prose land under one document | [test_pdf_table_extraction.py](tests/test_pdf_table_extraction.py) |
| — | **Universal ingest** — dialect detection (encoding / delimiter / decimal, each with provenance and confidence) + three-signal column resolution. Fixtures assert the **total**, not merely that the file parsed | [dialect.py](services/tabular/dialect.py), [column_resolver.py](services/tabular/column_resolver.py) |
| — | **Trust Report** — ingest evidence persisted in `csv_schemas.ingest_json`; findings recomputed live so a report can never disagree with the data it describes | [ingest_report.py](services/tabular/ingest_report.py) |

### v4.2–v4.5 — Enrichment, MCP, chat, meetings

| ID | Item |
|---|---|
| v4.2 | Market data — pluggable `MarketDataProvider` (yfinance) + `get_price_history` / `get_security_classification` / `get_corporate_events` / `enrich_holdings` |
| v4.3 | MCP polish — per-collection guide memory, `find_in_documents`, structured `rows`, `identifier_type` disambiguation, `suggested_next` hints |
| v4.4 | In-app chat — SSE tool-use loop over Anthropic + OpenAI, live tool cards, sources panel, BYO-key, `/brief` |
| v4.4.1 | Chat orchestration — ~920 LOC lifted out of `main.py` into [services/chat/](services/chat/); real provider token streaming; SSE for slash commands. *Whole-loop streaming deferred* |
| v4.4.2 | Provider capability clarity — `ProviderCapabilities` + `KNOWN_MODELS`; `probe_capabilities()` catches "claims OpenAI-compat, can't do tools" |
| v4.4.3 | Advisor desktop UX — R1 onboarding, R2 provider config, R3 collection summary, R4 brief modal (print stylesheet), R5 chat polish, R6 recording, R7 Note of Record, R8 Basic Mode, R9 privacy surfacing, R10 stability. *§13 manual signoff open* |
| v4.4.4 | Anthropic alignment — prompt caching (~48% input-token savings observed), native Citations, extended thinking on Claude 4, model-default refresh. *Compaction open* |
| v4.4.5 | Provider tiers — Default (Ollama Cloud, `gemma4:31b`) / Quality (Anthropic) / Sovereignty (self-hosted) shipped; [COMPLIANCE.md](COMPLIANCE.md) one-pager revised 2026-08-11 with the three-boundary table and verbatim dated vendor quotes. *Bedrock + tier-aware onboarding open* |
| v4.5 | Meeting capture — audio ingest via faster-whisper; `MeetingNotes` / `ActionItem` extraction with a conservative, injection-guarded prompt; `MeetingNotesStore`; `get_meeting_notes` / `list_action_items`. *`prep_for_meeting` + first-class `meetings` doctype open* |
| v4.6 | **Client profile / IPS** — typed per-collection store (risk tolerance, horizon, goals, household, target allocation + bands, concentration ceiling, prohibited holdings, tax posture, liquidity), drift engine, form UI, `get_client_profile` tool, `completeness.blocked` naming every judgment prep cannot make without it. `api/` routes, 22 tests. See [client_profile.py](services/financial/client_profile.py), [ips_drift.py](services/financial/ips_drift.py) |
| v4.6 | **`prep_for_meeting`** — the §7 keystone. Deterministic composite of meeting history, aged action items, portfolio, IPS policy, TLH, and corporate events into a priority-ordered agenda plus a first-class `gaps` list. No LLM. 27 tests. See [meeting_prep.py](services/meeting_prep.py) |
| v4.6.1 | Tax-Loss Harvesting — `scan_unrealized_losses`, `gain_loss_budget`, `suggest_replacements` (never substantially-identical), `check_wash_sale` across the **household including spousal IRA/Roth** — the moat over single-account tools. 57 tests |
| v4.9 | Dashboard — apexcharts, six chart-first brief sections with `<details>` table fallbacks and print handling, Overview tab as default landing, action-items endpoint |
| v4.10 | Outbound — Friday what-changed digest + daily morning brief (APScheduler tick, Resend transport, per-user opt-in, 23h idempotency) |

### Infrastructure and hygiene

- **Ollama Cloud model selection** — four stacked bugs fixed (model discovery had never
  worked); free-tier usage tiers scraped and encoded, with
  [scripts/refresh_ollama_cloud_tiers.py](scripts/refresh_ollama_cloud_tiers.py) exiting
  non-zero on drift.
- **Audit-log isolation** — the test suite had been appending invented client names to
  the real `data/redaction_log.db` through the module singleton, and the Boundary Report
  renders straight off that table. [tests/conftest.py](tests/conftest.py) now redirects it
  suite-wide.
- **[tests/test_vue_template_hygiene.py](tests/test_vue_template_hygiene.py)** — guards
  the `.value`-in-template class of bug that crashed the provider model picker.
- **SAPHIRE/NRC purge** and Electron / OCR-playground / tokenizer removal — bundle
  3,764 kB → 1,710 kB (−55%).
- **Tool-surface reachability (2026-08-12)** — the four MCP-only meeting-loop tools added
  to the chat registry, and the v4.6 keystones un-buried from the collection-kind filter
  that had been dropping them on Anthropic/OpenAI since they shipped. Two invariant tests
  now enforce registry ↔ kind ↔ dispatcher agreement. See §11 #3.
- **Untrusted-content guardrail (2026-08-12)** — `_UNTRUSTED_CONTENT_RULE` on every
  assembled system prompt, with expertise packs carved out as authoritative. See §8 #1.
- **[TESTING.md](TESTING.md) (2026-08-12)** — the tester-facing quickstart, guided pass,
  and known-issues table. See §7.
- **`run.sh` / `run.bat` port reporting** — both hardcoded 8000; they now resolve `PORT`
  from `.env` and print the real URL.
- **Authentication (2026-08-12)** — [access_auth.py](services/access_auth.py) verifies
  Cloudflare Access JWTs (JWKS + `aud` + `iss`); multi-user mode 401s instead of falling
  back to `default_user_id`; `/api/users` gained the auth dependency it never had; CORS
  became configurable. [DEPLOYMENT.md](DEPLOYMENT.md) is the runbook,
  [scripts/reassign_owner.py](scripts/reassign_owner.py) the ownership migration. 20
  tests. See §7.

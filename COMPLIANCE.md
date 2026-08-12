# Finn — Compliance Brief for Financial Advisors

This document is written for the compliance officer (CCO) of an RIA, broker-dealer, or wealth-management firm evaluating Finn. It exists so the advisor introducing Finn does not have to re-answer the same compliance questions from scratch every time.

It covers:

1. How Finn handles client data
2. The four LLM-provider tiers Finn supports, with a per-tier compliance summary and a list of known gaps
3. How Finn's controls map onto SEC Reg S-P, the FTC Safeguards Rule, and related FINRA expectations
4. A written-information-security-program (WISP) template the firm can adapt

**This document is not legal advice.** Every commitment cited below is verifiable at the linked source on the date listed. Compliance posture changes — the firm's CCO is responsible for re-verifying the underlying vendor language before adopting any tier.

**Last updated:** 2026-08-11.

---

## 1. How Finn handles client data

Finn is **self-hosted by default**. The advisor installs the desktop app (or runs the Docker stack) on hardware they control. The flow is:

1. **Ingest** — custodian CSVs, PDFs, transcripts, and other documents land in a local SQLite + FAISS index on the advisor's machine. No copy is uploaded to a Finn-operated server. There is no Finn-operated server.
2. **Retrieve** — chat questions and `/mcp` tool calls run against the local index. Retrieval itself never leaves the box.
3. **Redact** — every payload that crosses to an external LLM is scrubbed by a Microsoft Presidio–based pipeline (`services/privacy/`) plus custom recognizers for financial-account numbers, routing numbers, and CUSIP-in-context. A local SQLite audit log records every redaction with a before/after pair, and the original text is stored *locally only*.
4. **Reason (optional)** — the redacted context is sent to whichever LLM the advisor configured. The advisor brings their own API key (BYO-key); Finn does not proxy traffic. Token costs flow on the advisor's account, not Finn's.

### The three paths to an external LLM

"Redaction happens at the output boundary" is precise only if the boundary has no gaps. Finn has exactly three paths by which data derived from a client document can reach an external model, and each is covered by a named control:

| Path | What travels | Control | Where |
|---|---|---|---|
| **Tool results** — the model calls `query_table`, `search_collection`, `get_meeting_notes`, etc. | Query results, schema previews, meeting notes | Presidio redaction on every tool response before it is serialized | `mcp_server._redact` → `redaction_middleware.redact_mcp_response` |
| **Retrieved chunks** — passages the model is given up front to answer from (RAG) | Passages from PDFs, meeting transcripts, notes of record | Presidio redaction at prompt-construction time | `services/chat/context.py` → `redaction_middleware.redact_text_for_ai` |
| **Holdings tables** — position data inlined into the prompt | Symbols, quantities, market values, cost basis | Column-level sanitization at **ingest**, before anything is written to disk: account numbers and holder names are replaced with a stable hash (`[ACCT-3F2A]`), and address / phone / email / SSN / client-name columns are dropped outright | `services/privacy/column_sanitizer.py`, called from `services/indexing/indexer.py` |

The third path is worth the CCO's attention because it is stronger than redaction, not weaker: PII in tabular custodian exports is removed *before it is persisted*, so it is absent from the search index and the SQL store, not merely filtered on the way out. The advisor already has every account number in their custodian portal; Finn's index does not need them.

**Person names are pseudonymized, not blanked.** The default redaction style is `consistent_pseudonym`: "Robert Henderson" leaves the boundary as a stable stand-in name rather than `[PERSON]`. This is a deliberate correctness choice. A meeting transcript naming three people collapses under `[PERSON]` tokens into text where the model can no longer tell who agreed to what — the boundary would be buying privacy with wrong answers. The mapping is a SHA-256 derivation, so the same real name maps to the same stand-in for the life of the deployment, and no real name crosses the boundary either way. A firm that prefers hard tokens sets `redaction_style: entity_type` per collection (§4, technical safeguards).

Account numbers, SSNs, routing numbers, IBANs, emails, and phone numbers are *not* pseudonymized — those render as `[ACCOUNT_NUMBER]`, `[SSN]`, and so on.

The data layer is **local-first**. The intelligence layer is **whatever LLM the firm has approved**. Finn is the boundary that makes the second one safe to use against the first.

### Finn-side controls (every tier)

| Control | Where it lives | What it does |
|---|---|---|
| PII / CUI redaction on tool output | `services/privacy/redaction_engine.py` + `redaction_middleware.py` | Wraps every MCP tool response; replaces detected entities with stable pseudonyms or `[ENTITY_TYPE]` tokens before any external transmission |
| PII / CUI redaction on retrieved chunks | `services/chat/context.py` | Same engine, applied to RAG passages as the prompt is assembled. Redaction is applied to the *outbound payload only* — the advisor's own sources panel still shows the real text, because the advisor is authorized to see it |
| Ingest-time column sanitization | `services/privacy/column_sanitizer.py` | Tabular PII never reaches disk: account numbers and holder names are hashed to `[ACCT-XXXX]` (preserving per-account grouping), and address / phone / email / SSN / DOB / client-name columns are dropped before indexing |
| Custom financial recognizers | `services/privacy/custom_recognizers/` | `FinancialAccountRecognizer`, `RoutingNumberRecognizer`, `CUSIPInContextRecognizer` — augments standard Presidio entities (PERSON, EMAIL, PHONE, SSN, IBAN) with finance-specific identifiers |
| Per-collection redaction policy | `services/privacy/redaction_config.py` | The CCO can tighten or loosen redaction style per client collection — `[ENTITY_TYPE]`, `consistent_pseudonym`, `partial_mask`, allow-list, score threshold, strict-mode |
| Local audit log | `services/privacy/redaction_log.py` (SQLite) | Records redaction events with original text stored *locally only*. `GET /api/redactions/{summary,log}` exposes the running tally to the chat-tab "PII pill" |
| Boundary Report | `services/privacy/boundary_report.py` → `RedactionBoundaryPanel.vue` | Renders the audit log as what it means rather than what it logged: substitutions grouped by the boundary they crossed, repeated mentions of one value collapsed with an occurrence count, and — on explicit request — the local original beside the stand-in that was transmitted. Reachable from the chat PII pill and the Overview tab; no AI provider is called to produce it |
| Pre-flight review on tabular uploads | `services/privacy/pii_preflight.py` | Before a CSV/XLSX is committed to the index, the advisor sees what would be redacted; can blacklist a column entirely |
| Document-level prompt-injection scan | `services/prompt_injection_detector.py` | Flags retrieved documents that contain text resembling indirect prompt-injection (persona injection, "ignore previous instructions," etc.) before they reach an LLM |
| All-local default | App boots without any external provider configured. Search, retrieval, MCP tools all work on local data without an LLM call | The advisor must explicitly paste an API key in Settings to enable any external transmission |

These controls run on **every tier below**. The tier choice changes which external LLM is in scope; the Finn-side guardrails are constant.

---

## 2. The four LLM-provider tiers

Finn's BYO-key model means the firm picks the LLM provider. Because compliance review is the real friction (not technical capability), Finn supports four named tiers ordered by the firm's compliance posture:

| Tier | Provider | Primary fit | Quick read |
|---|---|---|---|
| **Default** | Ollama Cloud (default model `gemma4:31b`; gpt-oss, qwen3, kimi-k2 and others selectable) | Solo RIAs, small advisor shops, evaluation | Cheapest tool-use-capable models. Published no-retention and no-training commitments (quoted below), but unattested — no SOC 2 today. Acceptable when paired with Finn's redaction *and* documented residual risk in the firm's WISP |
| **Quality** | Anthropic API direct (Claude Sonnet 4.6 / Opus 4.7) | Mid-market RIAs ($100M – $1B AUM) | SOC 2 Type II, signed DPA available. No FedRAMP, not HIPAA-eligible on the direct API path |
| **Enterprise** | AWS Bedrock (Anthropic / Llama / Mistral) | $1B+ AUM, broker-dealer affiliates, federal-contractor exposure | FedRAMP High, HIPAA-eligible (BAA), ISO 27001/17/18, IRAP, IL5. Higher per-token cost; not yet implemented in Finn — tracked in roadmap v4.4.5 |
| **Sovereignty** | Self-hosted Ollama on firm-controlled hardware | Firms with hard data-residency rules; international jurisdictions with US-transfer restrictions | Zero data leaves the firm's hardware. Quality lag vs frontier models on tool-use accuracy is the real cost |

The tier the firm picks is a **business + compliance decision**, not a technical one. Finn supports all four; the same data layer, the same tools, the same redaction boundary.

---

### Tier 1 — Default: Ollama Cloud

**What it is.** Ollama Cloud is a hosted inference service for the open-weights models Ollama distributes. Finn uses it via the `OllamaCloudProvider` in [services/ai_service.py](services/ai_service.py) and defaults to `gemma4:31b`. The models are open-weights — no model vendor holds the firm's traffic under separate terms the way a proprietary frontier model would; the only party in scope is Ollama itself and whatever inference infrastructure it uses.

**Vendor commitments (verify at source).**

- Privacy Policy: <https://ollama.com/privacy> — the version reviewed here is marked **"Last updated: March 2026"**
- Terms of Service: <https://ollama.com/terms>

Ollama's published position on retention and training is explicit rather than inferred. Quoted verbatim from the Privacy Policy:

> "When using cloud-hosted models, we process this content transiently to provide the Service and this content is not stored beyond the time required to fulfill the request."

> "We do not use your inputs or outputs to train any AI models or request prompt or response content in support requests."

> "We may collect limited device and usage metadata (such as app version and request counts) that does not include your prompt or response content."

Read together, these are the three commitments a CCO asks for on an inference vendor: **no retention beyond the request**, **no training on inputs or outputs**, and **operational telemetry that excludes content**. The third matters more than it looks — many vendors promise no training while retaining prompt logs for abuse monitoring; Ollama's language puts prompt and response content outside the metadata they collect at all.

**What this is and is not.** It is a public policy commitment, unilaterally amendable by the vendor, not a negotiated contract with the firm. It is not third-party attested — there is no SOC 2 report testing whether the transient-processing claim holds in practice. The CCO should re-verify the language at the URL above on the date of approval and retain a dated copy, because the "Last updated" stamp is the only version control the firm gets.

**Known gaps for regulated advisor use.**

| Gap | Implication | How to address |
|---|---|---|
| No published SOC 2 Type II | The no-retention commitment is unattested. Cannot evidence controls testing to a regulator on the vendor's behalf | Document as a residual risk in the WISP; rely on Finn's redaction boundary as the compensating control |
| No standard DPA / signed contract | The firm's data-handling commitments to its clients aren't backstopped by a vendor contract | Use the firm's standard vendor questionnaire; treat the dated Privacy Policy as the binding representation; document the gap |
| No published SLA | No vendor-side commitment on uptime or breach notification | Acceptable for non-critical advisor workflows; document in the WISP |
| Subprocessor list is general, not enumerated | The policy names categories — "Stripe for payments, cloud infrastructure providers, model inference providers" — rather than named entities. **"Model inference providers" is the material one**: it indicates some cloud models may be served by downstream infrastructure, so the no-retention commitment depends on Ollama's flow-down to those parties, which is not published | Ask Ollama directly for the current named list before approval. The firm's client privacy notice should disclose the LLM-provider category rather than attempt sub-subprocessor enumeration |

**Why this tier still works for solo / small RIAs.** Finn's redaction boundary ensures **what crosses the boundary is not client identifiers** — it is redacted strings tagged `[PERSON]`, `[ACCOUNT_NUMBER]`, `[CUSIP]`, etc. The unredacted record stays local. The CCO documents this fact pattern as the residual-risk treatment in the firm's WISP under both:

- **SEC Reg S-P 17 CFR §248.30** — "reasonably designed administrative, technical, and physical safeguards" for protecting customer information
- **FTC Safeguards Rule (16 CFR Part 314)** — applies to non-bank financial institutions including investment advisers; requires a written information security program with risk assessment and service-provider oversight

A documented residual-risk treatment that names the specific data crossing the boundary (redacted strings, never raw PII) and the specific control that ensures it (Presidio + custom recognizers + audit log) is a defensible posture for solo and small RIA compliance reviews. **The CCO should still review and sign off; this is not an automatic approval.**

**Recommended for:** solo RIAs, small advisor shops (1–5 advisors), firms in evaluation. **Not recommended for:** mid-market and above without a documented exception.

---

### Tier 2 — Quality: Anthropic API direct

**What it is.** Direct API access to Anthropic's Claude models — Sonnet 4.6 for the default chat path, Opus 4.7 for long-context analysis (`/brief`, `/tlh`, `/rebalance`, `prep_for_meeting`). Finn uses this via the `AnthropicProvider` in [services/ai_service.py](services/ai_service.py). Prompt caching is enabled; native Citations are wired so source attribution is structured rather than prose-parsed.

**Vendor commitments (verify at source).**

- Trust Center: search "Anthropic Trust Center" — published SOC 2 Type II, ISO 27001, control descriptions
- Commercial Terms / DPA: published; available on request through Anthropic's commercial channel
- Subprocessor list: published in the Trust Center

The substance of Anthropic's published commercial-tier position:

- **SOC 2 Type II** with annual recertification
- **ISO 27001** certified
- **Signed DPA** available on request, including SCC + UK IDTA addenda for international transfers
- **Named subprocessor list** with onward-transfer commitments
- **No training on commercial-tier API traffic by default** (the consumer Claude.ai product has a different stance — confirm the firm is on the API tier, not the consumer tier)
- **Zero Data Retention (ZDR)** is available on request for qualifying customers; this further tightens the no-retention story

**Known gaps for regulated advisor use.**

| Gap | Implication | How to address |
|---|---|---|
| No FedRAMP authorization on the direct API | Federal-contractor exposure or government-adjacent client work needs FedRAMP-authorized infrastructure | Move to Tier 3 (Bedrock — Anthropic models on FedRAMP-authorized AWS GovCloud) |
| Not HIPAA-eligible on the direct API | A BAA is not offered on the direct Anthropic API path | Move to Tier 3 (Bedrock) where Anthropic models are HIPAA-eligible under AWS's BAA |
| Cross-border transfer to US | EU / UK / APAC firms need SCC / UK IDTA executed and a transfer impact assessment | Standard SCC available on request; the firm's DPO completes the TIA |

**Recommended for:** mid-market RIAs ($100M – $1B AUM); firms that prioritize answer quality and have a CCO comfortable with a SOC 2 Type II + DPA posture. **Not recommended for:** firms with FedRAMP, HIPAA, IL5, or IRAP requirements (use Tier 3 instead).

---

### Tier 3 — Enterprise: AWS Bedrock (planned)

**Status:** open in the roadmap (v4.4.5 §"AWS Bedrock provider"). **Build is gated on real customer pull** — Finn has not built the `BedrockProvider` ahead of an enterprise customer asking, because the Bedrock-Anthropic API surface differs from the direct Anthropic API and the wrapper is non-trivial. When an enterprise customer requires it, the build is 2–3 days.

**What it would be.** Anthropic's Claude models, plus Llama and Mistral, served through AWS Bedrock. Finn would wrap the Bedrock SDK and route enterprise-tier traffic through it.

**Vendor commitments (AWS Bedrock — verify at source).**

- AWS Compliance: <https://aws.amazon.com/compliance/>
- AWS Artifact (compliance-document portal): accessible from the AWS console for active accounts
- Anthropic on Bedrock: covered under the AWS shared-responsibility model and the AWS BAA when the firm has executed one

The substance of AWS Bedrock's published position relevant to advisors:

- **FedRAMP High** authorization
- **HIPAA-eligible** under the AWS BAA
- **ISO 27001 / 27017 / 27018** (information security / cloud-specific / PII in clouds)
- **IRAP** (Australian government)
- **IL5** (US DoD Impact Level 5) on AWS GovCloud (US)
- **SOC 1, SOC 2, SOC 3** Type II reports available through AWS Artifact
- **Region pinning** — the firm chooses the AWS region; data does not leave it for inference
- **Bedrock-tier Anthropic models do not retrain on customer prompts** — same posture as direct Anthropic, plus AWS's contractual envelope

**Recommended for:** $1B+ AUM RIAs, broker-dealer affiliates, advisors with federal-contracting exposure, healthcare-adjacent client bases (high-net-worth physician clients, etc.), international firms needing IRAP / IL5 compliance.

**Tier-3 gating in Finn:** when this provider lands, the Settings tab will surface a Tier-3 badge and the WISP template (§4 below) ships a Tier-3 supplement.

---

### Tier 4 — Sovereignty: Self-hosted Ollama

**What it is.** A local Ollama instance (or a self-hosted GPU server) running open-weights models on hardware the firm controls. Finn uses this via the `OllamaProvider` in [services/ai_service.py](services/ai_service.py) — the same code path as Tier 1 with the base URL pointed at the firm's own Ollama server.

**Vendor commitments.** *None — the firm is the vendor.* The "no data exfiltration possible" story is a function of the network boundary the firm controls, not a contractual promise.

**Known gaps.**

| Gap | Implication | How to address |
|---|---|---|
| Quality lag vs frontier | Local 70B-class open-weights models lag Sonnet 4.6 / Opus 4.7 on tool-use accuracy. The agent will still answer; some answers will be worse | Acceptable when sovereignty is the binding constraint. The v4.6.2 eval suite will publish a comparison number once it lands |
| Hardware cost | An Ollama-capable GPU server is a real capital expense | Standard IT cost; firms with this requirement typically already have the budget |
| Operational burden | The firm operates the inference server: patching, monitoring, scaling, backup | Same posture as any other on-prem service |

**Recommended for:** firms with absolute data-residency requirements (some EU jurisdictions, GCC, federal-adjacent contractors with no cloud authorization), firms recovering from a prior cloud-vendor incident, firms whose written WISP commits to "no client data leaves firm-controlled hardware."

---

## 3. Regulatory mapping

Two US frameworks are load-bearing for advisors evaluating Finn. International firms have analogues (UK GDPR, EU GDPR + DORA, APAC residency rules); the structure of the analysis is the same — name the data, name the boundary, document the residual risk.

### SEC Regulation S-P (17 CFR Part 248)

Reg S-P §248.30 requires "reasonably designed administrative, technical, and physical safeguards" for protecting customer records and information. The 2024 amendments added incident-response and customer-notification requirements.

| Reg S-P expectation | Finn-side answer |
|---|---|
| Written policies and procedures | The WISP template in §4 below covers Finn's role. The firm extends it with their own incident-response and notification language |
| Risk assessment of vendors | This document is the vendor-risk-assessment artifact for Finn itself. The LLM-provider tier choice is a separate vendor risk — covered per-tier above |
| Service-provider oversight | Finn is self-hosted; the LLM provider is the service provider whose oversight matters. Tier choice + Finn's redaction boundary is the oversight pattern |
| Incident response | Local audit log (`redaction_log.py`) is the forensic baseline. Finn does not have a "phone home" path — there is no Finn-side incident-detection capability the firm could rely on |
| Customer notification | The firm's own incident-response plan governs. Finn's local-first architecture means a breach of the LLM provider does not automatically constitute a breach of customer records (because what was sent was redacted) — this is documented in the WISP |

### FTC Safeguards Rule (16 CFR Part 314)

Applies to non-bank financial institutions including investment advisers. Requires a written information security program (WISP) with nine elements; the relevant ones for the Finn deployment are:

| Safeguards Rule element | Finn-side answer |
|---|---|
| §314.4(b) Risk assessment | The firm's risk assessment must explicitly cover the LLM-provider tier; this document supplies the per-tier risk profile |
| §314.4(c) Safeguards | Encryption in transit (HTTPS to LLM provider — enforced by the SDK); access control (local OS-level); change management (Finn ships installable releases with version pinning) |
| §314.4(d) Continuous monitoring or annual penetration testing | Finn surfaces a `/api/diagnostics/cache` endpoint and an audit log; the firm's monitoring stack scrapes those. Penetration testing is the firm's responsibility |
| §314.4(f) Service providers | Finn itself is a tool the firm operates — it is not a service provider receiving customer information. The LLM provider is the service provider; Tier table above covers each |
| §314.4(h) Written incident response plan | Template in §4 below |

### FINRA expectations

FINRA-registered firms (broker-dealers and dual-registrants) inherit Reg S-P + the firm's own FINRA Rule 4370 (Business Continuity Plan) requirements. Finn does not change either; the firm's existing BCP / IRP language extends to cover Finn the same way it covers any other locally-installed advisor tool.

---

## 4. Written information security program — Finn supplement (template)

The firm's CCO copies this into the firm's existing WISP as a supplement. **Replace bracketed placeholders with firm-specific values.** This is a starting template, not a finished document.

```markdown
## Written Information Security Program — Supplement: Finn

**Effective date:** [DATE]
**Owner:** [CCO NAME / TITLE]
**Last reviewed:** [DATE]

### 1. Scope

This supplement addresses the firm's use of Finn, a self-hosted document-search and
analysis application, for storing and analyzing client portfolio data and meeting
artifacts.

Finn is installed on:
- [LIST DEVICES — advisor laptop, dedicated office workstation, on-prem server]

The firm has selected the following LLM-provider tier (see Finn's COMPLIANCE.md):
- [ ] Tier 1 — Ollama Cloud (Default)
- [ ] Tier 2 — Anthropic API direct (Quality)
- [ ] Tier 3 — AWS Bedrock (Enterprise)
- [ ] Tier 4 — Self-hosted Ollama (Sovereignty)

### 2. Data inventory

The following customer-information categories are processed within Finn:
- Holdings positions (account number, symbol, cost basis, market value)
- Account ownership (client name, household relationship)
- Transcripts of client meetings (audio → text via local Whisper)
- Notes of record and meeting briefs derived from the above

All categories are stored locally on the device(s) listed in §1. Finn does not
transmit raw customer information to any third party. When the LLM-provider
tier is invoked, the data crossing the boundary is redacted strings (entity
tokens or deterministic pseudonyms), not raw identifiers.

### 3. Administrative safeguards

- Access to the device(s) in §1 is restricted to [ROLES].
- The firm reviews this supplement [ANNUALLY / SEMI-ANNUALLY].
- The firm maintains a record of LLM-provider tier changes; any tier change
  requires CCO approval and an update to this supplement.

### 4. Technical safeguards

- Full-disk encryption is enabled on every device in §1: [ATTESTATION OR REFERENCE].
- Operating-system access requires authentication: [METHOD — password, biometric, SSO].
- Finn's redaction policy for each client collection is set to [REDACTION STYLE —
  default is `consistent_pseudonym`, which replaces person names with stable
  stand-in names; `entity_type` substitutes hard [PERSON] tokens instead]
  with a score threshold of [THRESHOLD]. The firm reviews the redaction policy
  per collection at onboarding and again annually.
- The firm enables strict-mode redaction for any collection containing
  [SPECIFY — e.g., minor children, healthcare-adjacent identifiers, attorney-client material].

### 5. LLM-provider oversight

The firm has reviewed the published privacy and security commitments of the
selected provider tier (see Finn's COMPLIANCE.md §2). The CCO has documented
the residual risks specific to the selected tier and the compensating controls.

[INSERT TIER-SPECIFIC RESIDUAL-RISK STATEMENT — copy from COMPLIANCE.md §2 and
sign at the bottom of this supplement.]

### 6. Incident response

If a Finn-related incident is suspected:

1. Disconnect the affected device from external networks immediately.
2. Preserve the local Finn audit log at `data/redactions.db`.
3. Notify [INCIDENT-RESPONSE LEAD] within [HOURS].
4. Determine whether the incident involved data that crossed the LLM-provider
   boundary. If so, the LLM provider's published incident-notification
   procedure applies in addition to the firm's own.
5. If customer information was exposed, the firm's standard customer-notification
   procedure under SEC Reg S-P 17 CFR §248.30(a)(3) is initiated by [ROLE].

### 7. Records and retention

- Finn's local redaction audit log is retained for [N] years.
- Finn's local document index and chat-thread storage is retained per the firm's
  general client-record retention policy.
- Backup procedure for the device(s) in §1: [PROCEDURE].

### 8. Review and approval

This supplement was reviewed and approved by:

[NAME / TITLE / DATE / SIGNATURE]
```

---

## 5. Frequently asked questions

**Q: Does Finn store any customer data on Anthropic / OpenAI / Ollama servers?**
No. The data layer is local. Only redacted strings cross the boundary when the advisor invokes a chat that needs an LLM, and only to the LLM provider the firm has approved. On the default tier, Ollama's Privacy Policy separately commits that cloud-hosted model content "is not stored beyond the time required to fulfill the request" — so the firm gets both a Finn-side control (nothing identifying is sent) and a vendor-side commitment (what is sent is not retained). The Finn-side control is the one the firm can verify itself; see the verification question below.

**Q: What exactly does Ollama retain, and can we show an investor or regulator?**
Per the Privacy Policy dated March 2026, quoted in §2 Tier 1: content sent to cloud-hosted models is processed transiently and not stored beyond the request; inputs and outputs are not used to train any AI models; and the usage metadata Ollama does collect (app version, request counts) explicitly excludes prompt and response content. Retain a dated copy of <https://ollama.com/privacy> at the time of approval — a public policy is unilaterally amendable and the "Last updated" stamp is the only version control available. Note the residual dependency: the policy lists "model inference providers" among the third parties Ollama uses, and the flow-down of the no-retention commitment to those downstream parties is not published.

**Q: Can the firm prevent any external transmission?**
Yes. Tier 4 (self-hosted Ollama) routes 100% of inference to firm-controlled hardware. There is no "phone home" path in Finn itself — search, retrieval, and MCP tools all work without any external provider configured.

**Q: What happens if Anthropic / Ollama / AWS has a breach?**
Because Finn redacts before crossing the boundary, the data potentially exposed at the LLM provider is redacted strings, not raw client identifiers. The firm's incident-response plan still applies, but the blast radius is constrained by the redaction guarantee.

**Q: How does the firm verify the redaction is actually happening?**
- **Without leaving the app:** open the Boundary Report — the shield pill beneath the chat input, or the foot of the Overview tab. It lists each identifier that was replaced, grouped by the boundary it crossed, with an occurrence count. "Show the real values" adds a column holding the original from the local record beside the stand-in that was actually transmitted, so the substitution can be read directly rather than inferred. That column is fetched only when requested and is never included otherwise.
- The local audit log (`data/redactions.db`) is the underlying record: every redaction event, with the original text stored locally. Each event carries the boundary it fired at, so the CCO can distinguish a redaction on a tool result from one on a retrieved chunk (`tool_name = "chat_context"`) or on advisor-drafted output (`notes_output`, `followup_output`).
- `GET /api/redactions/summary` and `GET /api/redactions/log` expose the running tally; `GET /api/redactions/boundary-report` returns the grouped view the panel renders.
- `POST /api/redactions/dry-run` lets the CCO see what would be redacted from any text payload without executing it.
- Regression tests pin the behavior and can be re-run against the installed version: `pytest tests/test_pii_redaction.py tests/test_redaction_http.py tests/test_chat_context_redaction.py`. The last of these asserts the two-sided invariant directly — that client names and account numbers are absent from what is sent to the model, *and* that the advisor's own sources panel still shows the real text.

**Q: Does Finn collect telemetry?**
No. There is no Finn-operated analytics, error-reporting, or usage-tracking service. The application boots, runs locally, and emits no outbound traffic except to the LLM provider the firm configured.

**Q: What about transcripts and audio recordings?**
Whisper transcription runs locally (`faster-whisper`). The audio file and the transcript both live in the local Finn data directory and are subject to the same redaction boundary as any other document.

**Q: How does Finn handle the firm's own PII (firm employee names, advisor names)?**
The same way it handles client PII — Presidio's `PERSON` recognizer applies. The firm can add an allow-list to prevent over-redaction of names that appear in firm-internal contexts.

**Q: Can the firm tighten the redaction policy further?**
Yes. `services/privacy/redaction_config.py` exposes per-collection profiles: redaction style, score threshold, allow-list, strict-mode. The CCO can configure these from the Settings tab or directly via the redaction config file.

---

## 6. References within Finn's source

For the CCO who wants to verify a control rather than take this document's word for it:

- Redaction engine: [services/privacy/redaction_engine.py](services/privacy/redaction_engine.py)
- Redaction middleware (tool-output boundary): [services/privacy/redaction_middleware.py](services/privacy/redaction_middleware.py)
- Retrieved-chunk boundary (RAG): [services/chat/context.py](services/chat/context.py) — `_redact_for_llm`, `_format_retrieved_context`, `_build_citation_documents`
- Ingest-time tabular sanitization: [services/privacy/column_sanitizer.py](services/privacy/column_sanitizer.py), wired from [services/indexing/indexer.py](services/indexing/indexer.py)
- Custom financial recognizers: [services/privacy/custom_recognizers/](services/privacy/custom_recognizers/)
- Per-collection policy (incl. redaction style): [services/privacy/redaction_config.py](services/privacy/redaction_config.py)
- Local audit log: [services/privacy/redaction_log.py](services/privacy/redaction_log.py)
- Boundary Report: [services/privacy/boundary_report.py](services/privacy/boundary_report.py), [frontend/src/components/RedactionBoundaryPanel.vue](frontend/src/components/RedactionBoundaryPanel.vue)
- Pre-flight review on tabular uploads: [services/privacy/pii_preflight.py](services/privacy/pii_preflight.py)
- LLM provider implementations: [services/ai_service.py](services/ai_service.py)
- HTTP redaction endpoints: search [main.py](main.py) for `/api/redactions`
- Regression tests: [tests/test_pii_redaction.py](tests/test_pii_redaction.py), [tests/test_redaction_http.py](tests/test_redaction_http.py), [tests/test_chat_context_redaction.py](tests/test_chat_context_redaction.py), [tests/test_boundary_report.py](tests/test_boundary_report.py)

---

*Questions about this document or Finn's compliance posture: [maintainer contact — fill in for the firm's distribution].*

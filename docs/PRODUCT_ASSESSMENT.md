# Product assessment — September 12, 2026

## Judgment

Asymptote is useful as a **self-hosted knowledge workspace with an MCP server**.
Its strongest audience is developers and small teams working with curated,
sensitive documents. Its useful differentiation is deployment control, local
models, document citations, and access to the same knowledge from external AI
clients. Position it around those jobs.

The source is more capable than its opening documentation suggested: hybrid
retrieval, reranking, multiple extraction paths, SQLite/Postgres backends,
streamed chat, reports, structured-table queries, collection sharing, governance,
and Streamable HTTP MCP are already implemented. Most of the immediate product
work is making these capabilities coherent and trustworthy.

This is a source review and regression-tested improvement, **not an enterprise
certification or a measured RAG-quality benchmark**. No production corpus,
live model server, real identity provider, or Postgres instance was supplied.
The product is suitable for a controlled pilot; broad enterprise readiness needs
the validation and remaining work below.

## Product boundary

The primary workflow is **add sources → ask or find → verify a passage**.
MCP exposes that knowledge to an AI client the user chooses. Three destinations:
Ask, Find, Connect. A collection is the unit of organization and sharing.

Reports and reusable instructions support this workflow. They do not need to
compete with it in primary navigation. Notes open on demand. Administration
and model/retrieval configuration are secondary operator tasks.

Avoid expanding this into a general agent platform, financial terminal, or media
production suite. An MCP client marketplace, audio/video overviews, and dozens
of connectors would substantially increase maintenance before proving the core
private-knowledge workflow.

## Competitive comparison

Comparison uses official documentation retrieved on the assessment date. Feature
availability depends on edition and deployment; documentation is not a benchmark.

| Capability | Asymptote in this branch | Reference / remaining gap |
|---|---|---|
| Source-grounded Q&A and citations | Implemented; local or hosted generation | NotebookLM grounds answers in notebook sources. Measure citation correctness and abstention on your own corpus. |
| Curated source workspaces | Collections, source lists, chat history, sharing | NotebookLM makes the notebook the main workspace. This branch simplifies navigation around that pattern. |
| Enterprise retrieval | Semantic, keyword and hybrid search, filters, optional reranking | Onyx offers connected search across workplace systems. Retrieval quality and large-corpus latency here remain unmeasured. |
| Persistent connectors | File/folder/repository ingestion paths | No equivalent to Onyx's broad recurring connector sync, deletion pruning, sync health, and source ACL syncing. |
| Permissions | Opt-in private collections, verified Access identity, read/readwrite shares, admin gates | Collection permissions are not source-system ACL synchronization. Onyx documents permission sync for selected connectors as an Enterprise feature. |
| Private deployment | Self-hosting, local embeddings/models, offline configuration | Control of deployment is useful, but hosted embeddings/chat still send text externally. Enforce egress at the network layer when required. |
| MCP server | Streamable HTTP, search/context/table tools, resources, personal tokens, optional writes | Onyx also exposes private search over MCP. This branch adds read/write tool annotations and fixes explicit-token handling on open deployments. |
| MCP client | External clients connect to Asymptote | Onyx can additionally invoke external MCP servers. That is a distinct, deferred product scope. |
| Research outputs | Existing summaries, FAQs, timelines, briefings, study guides, Markdown export | NotebookLM additionally offers media and other Studio outputs. They are not implemented here. |
| Evaluation and operations | Regression tests; usage, governance and audit code | Needs a repeatable retrieval benchmark, connector operations, load tests, restore drills, and deployment-level security review. |

## Changes made

- Replaced six competing primary destinations with **Ask, Find, Connect**.
  Reports, saved instructions, administration, and notes remain accessible under
  More. Desktop and mobile use the same navigation component.
- Defaulted notes/tools to closed and removed source/notes panels from utility
  pages. Existing saved panel preferences remain respected.
- Made onboarding's search-only path useful and persistent. Clarified where
  sources are stored and when hosted providers receive text.
- Reordered MCP setup around the connection task; moved the tool inventory to a
  collapsed reference. Corrected the Claude project config filename to `.mcp.json`.
  Clarified that a URL's collection selection is a default, not an access limit.
- Required operator authorization for global MCP configuration in private mode.
  Token write permission now requires an actual JSON boolean; `"false"` can no
  longer become a write-enabled token through Python truthiness.
- Honored explicit MCP credentials even on open deployments, where the previous
  conditional middleware registration ignored their read-only/revocation state.
  Tightened token routing to `/mcp` and its children, excluding lookalike paths.
  Open deployments still trust unauthenticated local callers; token handling
  does not turn an open deployment into an authenticated one.
- Added MCP tool annotations distinguishing read-only operations from the
  potentially destructive, non-idempotent document writer. Annotations are client
  hints; existing server-side write authorization remains authoritative.
- Reauthorized cached source reads, excluded quarantined documents, tracked content
  hashes and effective sensitivity, and rejected incomplete provenance records.
  Similarity alone no longer permits an answer to a different question to be reused.
  Removed cached question text from routine cache logs. Cache clearing now checks
  collection write access or operator privileges.
- Bound Docker Compose's published port to loopback by default. **Upgrade note:**
  existing LAN users must set `BIND_ADDRESS` explicitly and protect ingress.

## Remaining priorities

| Priority | Next outcome | Acceptance criteria |
|---|---|---|
| P0 | Measure trustworthy retrieval | A versioned corpus with answerable, unanswerable, exact-identifier, negation, date, table, and cross-user cases; report recall@k, citation correctness, abstention, and p50/p95 latency. No quality score is claimed without these measurements. |
| P0 | Finish privacy and authorization review | Exercise every API/tool/resource with owner, reader, outsider, revoked token, and anonymous identities. Validate cloud egress across embeddings, OCR, reranking, and chat; confirm collection labels match actual policy enforcement. |
| P0 | Make caching reflect the entire request | Invalidate on corpus additions, collection guidance, attached instructions, and model/retrieval changes. Current cited-source checks do not detect every change to the wider corpus. Disable `ENABLE_ANSWER_CACHE` for pilots where that freshness is required. |
| P1 | Focus questions on selected sources | Source selection must constrain initial retrieval, subsequent tool calls, table queries, caches, citations, and exports end-to-end; a checkbox alone is insufficient. |
| P1 | Ship one dependable connector | Choose from actual pilot demand (e.g. a watched folder or SharePoint). Incremental sync, deletion propagation, resumable jobs, visible failure state, and permission tests must ship together. |
| P1 | Make deployment reproducible | Lock supported dependencies, validate the full container and Postgres path, perform backup/restore and upgrade drills, document model prerequisites and hardware-tested limits. |
| P2 | Broaden identity and MCP authentication | Add standard OIDC and deliberately scoped/expiring service tokens if pilots require them. Existing token collection_scope only grants restricted collections; it is not an all-collection allowlist. |
| P2 | Add selected research conveniences | Evidence-linked saved answers and source comparison before media generation or a broad agent-builder interface. |

## Validation

- Initial baseline: 342 backend tests and 12 frontend tests. Three initial
  provider-construction failures were resolved by installing the test environment's
  SOCKS proxy dependency; application code was not changed to bypass the proxy.
- Updated suite: 358 backend tests and 15 frontend tests pass.
- Production frontend build passes. ESLint reports zero errors and five existing
  `v-html` warnings; these were not silently disabled.
- Browser visual verification was blocked by Chromium download failures in the
  review environment. Desktop/mobile navigation is covered by component tests;
  a human visual review remains required before release.
- Backend tests use temporary databases and mocked model/provider boundaries.
  They do not establish model quality, real SSO behavior, Postgres parity under
  load, or successful air-gap deployment.

## Official references

- [Onyx RAG and Search](https://docs.onyx.app/overview/core_features/internal_search)
- [Onyx connectors and access controls](https://docs.onyx.app/admins/connectors/overview)
- [Onyx MCP server](https://docs.onyx.app/deployment/configuration/mcp_server)
- [Onyx MCP client actions](https://docs.onyx.app/admins/actions/mcp)
- [NotebookLM notebook context and grounding](https://support.google.com/notebooklm/answer/17003757)
- [NotebookLM reports and learning features](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-student-features/)
- [Workspace NotebookLM data protections](https://workspaceupdates.googleblog.com/2025/02/notebooklm-and-notebooklm-plus-now-workspace-core-service.html)
- [MCP Streamable HTTP requirements](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

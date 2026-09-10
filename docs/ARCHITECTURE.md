# Architecture

Asymptote is a privacy-focused tool for indexing your own documents and data, chatting with them through a grounded, citation-first assistant, and exposing them to external agents over MCP. Think "NotebookLM for files you own" — the intelligence lives in whatever LLM you trust; Asymptote owns the trustworthy data layer in front of it.

> The `master` branch is this general-purpose tool. Domain-specific financial-analysis features (brokerage ingest profiles, portfolio metrics, market-data tools, meeting briefs) live on the **`fintech`** branch, which is a superset of `master`.

## Backend (Python / FastAPI)

- [main.py](../main.py) — thin app assembly: middleware, lifespan (initializes the embedding model, vector store, indexer manager, MCP server, and collection service), router includes, and frontend serving.
- [api/](../api/) — HTTP endpoints, one router module per domain: `documents`, `search`, `chat`, `artifacts`, `collections`, `mcp`, `sharing`, `expertise`, `system`. Shared helpers/state (e.g. `get_indexer`, the expertise store singleton) live in [api/deps.py](../api/deps.py). Routers carry their full URL paths — no prefixes — so the public API matches the module layout one-to-one. Business logic belongs in `services/`, not in routers.
- [config.py](../config.py) — pydantic-settings `Settings` loaded from `.env`. Defaults are the safe solo posture (loopback bind, no CORS); a Postgres backend is opt-in. `main.py` runs a startup check that refuses or warns on configurations promising more safety than the app delivers — see [DEPLOYMENT.md](DEPLOYMENT.md).
- [models/schemas.py](../models/schemas.py) — every Pydantic request/response model.
- [services/](../services/) — feature modules:
  - `services/indexing/` — `DocumentIndexer` (chunk → embed → write to vector store).
  - `services/document_extractor.py` — generic text/tabular extraction across PDF, DOCX, TXT, Markdown, CSV/XLSX, audio (transcription), with header-sniffing for messy spreadsheet preambles.
  - `services/code_extractor.py` — language-aware extraction for source files.
  - `services/structured_store.py` — `StructuredStore`, a typed SQLite table per ingested CSV/XLSX sheet. Domain-agnostic: custom types and column-role detectors can be layered on via `register_type_extension` / `register_role_detector`.
  - `services/structured_chat.py` — the provider-agnostic chat tool registry and dispatcher (see below).
  - `services/agent_tools.py` — the same tool registry rendered as native Anthropic / OpenAI tool schemas.
  - `services/mcp_server.py` — embedded HTTP MCP endpoint mounted at `/mcp`.
  - `services/expertise_store.py` — named, markdown-body "Expertise" packs: reusable research frameworks/instructions a user can attach to a collection.
  - `services/content_policy.py` — ingest-time content-policy scanner (local regex rule packs, optional LLM second opinion) and the clear/flagged/quarantined/rejected decision.
  - `services/governance.py` — sensitivity labels, quarantine/approve/remove, the hash blocklist, reports, user suspension, and the acceptable-use acknowledgement. `services/audit.py` is the append-only trail behind all of it.
- **Governance choke points.** Quarantined documents are filtered in `DocumentIndexer.search` (every retrieval surface), in the MCP document tools, and by `governance.assert_document_servable` on the pdf/chunks endpoints. Restricted collections are refused by `sharing_service.create_share` and by `mcp_server._resolve_collection_id` / `_visible_collections` unless the MCP token is scoped to them. The AUP gate lives in `api/documents._require_ingest`. Every ingest path passes `uploaded_by` down to the indexer, which also hashes the file and consults the blocklist.
- [middleware/](../middleware/) — `user_context` resolves the owner recorded on collections. Multi-user mode is not supported (the app refuses to start with it enabled), so this always resolves to `default_user_id`; see [DEPLOYMENT.md](DEPLOYMENT.md) for why.

## Chat

Chat is a provider-agnostic ReAct-style tool-use loop, so it works across every provider the app supports (Anthropic, OpenAI, Ollama, and OpenAI-compatible endpoints) without per-provider tool-use APIs.

- The loop injects table schemas + tool-use instructions into the system prompt, the model emits `<tool_call>{...}</tool_call>` blocks (or the providers' native tool-calls), and [services/structured_chat.py](../services/structured_chat.py) executes them against the per-collection `StructuredStore` and document index, feeding results back on the next pass.
- The tool registry: `search_documents`, `get_document_context`, `list_tables`, `get_table_schema`, `get_table_rows`, `query_table`, `aggregate_table`, `list_collections`, `get_collection_info`. The in-app chat and the MCP endpoint expose the same primitives.
- The system prompt frames the assistant as a research assistant that grounds every claim in the provided sources, cites them, and says so plainly when the sources don't contain the answer.

## MCP

The embedded MCP server ([services/mcp_server.py](../services/mcp_server.py)) is mounted at `/mcp` for power users who already work inside an external MCP client.

The in-app chat is the primary surface; the MCP endpoint is secondary.

## Frontend (Vue 3 + Vite)

- [frontend/src/App.vue](../frontend/src/App.vue) — NotebookLM-style shell: Sources sidebar (left), main surface with tab navigation (Chat, Search, Generate, Expertise, MCP), Studio sidebar (right). Per-tab components in [frontend/src/components/](../frontend/src/components/) (ChatTab, SearchTab, ArtifactsTab, ExpertiseLibrary, MCPTab, SettingsTab, …).
- Pinia stores in [frontend/src/stores/](../frontend/src/stores/).
- The Vite dev server runs at `:5173` and proxies to the backend at `:8473`. The production build lands in `frontend/dist/` (gitignored) and is served by FastAPI with long-lived immutable caching for hashed `/assets/*` files; Docker images build it in a dedicated node stage.

## Design language

The UI follows the principles in [.impeccable.md](../.impeccable.md): the document is the subject, the app is the room. NotebookLM as reference, generic SaaS dashboard as anti-reference. Components stay theme-agnostic — use DaisyUI semantic tokens, never hard-coded colors.

# Architecture

Asymptote is a privacy-focused tool for indexing your own documents and data, chatting with them through a grounded, citation-first assistant, and exposing them to external agents over MCP. Think "NotebookLM for files you own" — the intelligence lives in whatever LLM you trust; Asymptote owns the trustworthy data layer in front of it.

> The `master` branch is this general-purpose tool. Domain-specific financial-analysis features (brokerage ingest profiles, portfolio metrics, market-data tools, meeting briefs) live on the **`fintech`** branch, which is a superset of `master`.

## Backend (Python / FastAPI)

- [main.py](../main.py) — single-file FastAPI app. All HTTP endpoints live here; business logic lives under `services/`. The lifespan handler initializes the embedding model, vector store, indexer manager, MCP server, and collection service.
- [config.py](../config.py) — pydantic-settings `Settings` loaded from `.env`. Defaults work for solo use; multi-user mode and a Postgres backend are opt-in.
- [models/schemas.py](../models/schemas.py) — every Pydantic request/response model.
- [services/](../services/) — feature modules:
  - `services/indexing/` — `DocumentIndexer` (chunk → embed → write to vector store).
  - `services/document_extractor.py` — generic text/tabular extraction across PDF, DOCX, TXT, Markdown, CSV/XLSX, audio (transcription), with header-sniffing for messy spreadsheet preambles.
  - `services/code_extractor.py` — language-aware extraction for source files.
  - `services/structured_store.py` — `StructuredStore`, a typed SQLite table per ingested CSV/XLSX sheet. Domain-agnostic: custom types and column-role detectors can be layered on via `register_type_extension` / `register_role_detector`.
  - `services/structured_chat.py` — the provider-agnostic chat tool registry and dispatcher (see below).
  - `services/agent_tools.py` — the same tool registry rendered as native Anthropic / OpenAI tool schemas.
  - `services/mcp_server.py` — embedded HTTP MCP endpoint mounted at `/mcp`.
  - `services/tabular/` — generic tabular inference (header detection, type/role mapping) shared by CSV/XLSX/PDF tables.
  - `services/expertise_store.py` — named, markdown-body "Expertise" packs: reusable research frameworks/instructions a user can attach to a collection.
- [middleware/](../middleware/) — `user_context` resolves `X-User-ID` for multi-user mode.

## Chat

Chat is a provider-agnostic ReAct-style tool-use loop, so it works across every provider the app supports (Anthropic, OpenAI, Ollama, and OpenAI-compatible endpoints) without per-provider tool-use APIs.

- The loop injects table schemas + tool-use instructions into the system prompt, the model emits `<tool_call>{...}</tool_call>` blocks (or the providers' native tool-calls), and [services/structured_chat.py](../services/structured_chat.py) executes them against the per-collection `StructuredStore` and document index, feeding results back on the next pass.
- The tool registry: `search_documents`, `get_document_context`, `list_tables`, `get_table_schema`, `get_table_rows`, `query_table`, `aggregate_table`, `list_collections`, `get_collection_info`. The in-app chat and the MCP endpoint expose the same primitives.
- The system prompt frames the assistant as a research assistant that grounds every claim in the provided sources, cites them, and says so plainly when the sources don't contain the answer.

## MCP

The embedded MCP server ([services/mcp_server.py](../services/mcp_server.py)) is mounted at `/mcp` for power users who already work inside an external MCP client.

The in-app chat is the primary surface; the MCP endpoint is secondary.

## Frontend (Vue 3 + Vite)

- [frontend/src/App.vue](../frontend/src/App.vue) — shell with tab navigation; per-tab components in [frontend/src/components/](../frontend/src/components/) (ChatTab, SearchTab, DocumentsTab, MCPTab, OCRPlaygroundTab, TokenizerTab, SettingsTab, ExpertiseLibrary, …).
- Pinia stores in [frontend/src/stores/](../frontend/src/stores/).
- The Vite dev server runs at `:5173` and proxies to the backend at `:8473`. The production build lands in [static/](../static/) and is served by FastAPI; do not hand-edit `static/assets/*` — they are build outputs.

## Design language

The UI follows the principles in [.impeccable.md](../.impeccable.md): the document is the subject, the app is the room. NotebookLM as reference, generic SaaS dashboard as anti-reference. Components stay theme-agnostic — use DaisyUI semantic tokens, never hard-coded colors.

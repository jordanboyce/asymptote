# Implementation Plan: Advisor Desktop UX

## Overview

Wire the existing capabilities (vendor profiles, brief generator, Note of Record, redaction engine, Whisper transcription, expert/basic mode flag) into a coherent, pilot-ready advisor flow. The plan is ordered so the **gating risks** land first — the things a pilot advisor would hit on day one — and polish/packaging falls at the end.

References to "Asymptote" in this spec and in [requirements.md](requirements.md) are pre-rebrand. Everything implemented here SHALL use "Finn".

### Audit of what already exists (treat as starting point, not a build-from-scratch list)

| Requirement | Already in tree | Gap |
|---|---|---|
| R1 Onboarding | [WelcomeOnboarding.vue](../../../frontend/src/components/WelcomeOnboarding.vue) (single-step Ollama Cloud key) | Single-step only; no privacy step, provider selector, or first-collection step |
| R2 AI Provider | `validate_api_key` ([main.py:1834](../../../main.py#L1834)), Settings tab present | Validation surfaced in onboarding only, not in Settings save flow |
| R3 Collection setup | Vendor profiles for [pershing](../../../services/ingest_profiles/pershing.yaml), [netx360](../../../services/ingest_profiles/netx360.py), [schwab](../../../services/ingest_profiles/schwab.yaml), [fidelity](../../../services/ingest_profiles/fidelity.yaml), [vanguard](../../../services/ingest_profiles/vanguard.yaml); upload service | Per-collection summary card (positions/accounts/most-recent-export-date) |
| R4 Meeting Brief | [services/brief_generator.py](../../../services/brief_generator.py), `/api/collections/{id}/brief` ([main.py:3397](../../../main.py#L3397)), chat-tab quick-action chip | No standalone "primary action on Collection view"; no print stylesheet; threshold UI |
| R5 Chat | `AgenticEngine`, `tool_start`/`tool_end`/`thinking` events already render in [ChatTab.vue](../../../frontend/src/components/ChatTab.vue) | Basic Mode collapses raw tool output into a "Thinking…" indicator only |
| R6 Recording | `MediaRecorder` + Whisper pipeline in [SourcesSidebar.vue:610](../../../frontend/src/components/SourcesSidebar.vue#L610) and [services/audio_transcriber.py](../../../services/audio_transcriber.py); upload-via-Files works | Recording control hidden in Sources sidebar; no top-level "Record meeting" affordance |
| R7 Note of Record | `/api/collections/{id}/notes` + `/notes/stream` ([main.py:3586](../../../main.py#L3586)), `_build_notes_prompt`, frontend `runNotesCommand` | No "Draft Note of Record" button when a transcript exists; redaction summary not shown alongside the draft |
| R8 Basic Mode | [`isExpertMode`](../../../frontend/src/utils/expertMode.js), nav filtered in [App.vue:1078](../../../frontend/src/App.vue#L1078) | Toggle is a header switch (good); but Settings panel doesn't fully hide MCP/OCR/dev panes in Basic Mode; no plain-language error mapping |
| R9 Privacy | `RedactionLog` SQLite ([services/privacy/redaction_log.py](../../../services/privacy/redaction_log.py)), middleware on every MCP tool | **No HTTP endpoints expose the audit log to the frontend**; no in-chat "redaction active" indicator; no dry-run preview |
| R10 Pilot readiness | App DB persists settings; standard installer path exists for desktop | No update-banner mechanism; no 7-day welcome-back hint; error boundary treatment |

---

## Tasks

- [ ] 1. Rebase the spec onto Finn
  - [ ] 1.1 Global find-and-replace `Asymptote` → `Finn` and `asymptote` → `finn` in [requirements.md](requirements.md)
  - [ ] 1.2 Update the introduction and glossary to read in the new name; verify no stale URLs

- [ ] 2. R1 — First-run onboarding (4 steps)
  - [ ] 2.1 Restructure [WelcomeOnboarding.vue](../../../frontend/src/components/WelcomeOnboarding.vue) into a step machine with 4 stages: `welcome` → `provider` → `collection` → `done`. Use a local `stage` ref + transition; back button on every stage except the first.
  - [ ] 2.2 Stage 1 (welcome): privacy explanation copy — local-only data, on-device PII redaction, what leaves the box. Single "Continue" CTA. Keep the trust-copy footer line.
  - [ ] 2.3 Stage 2 (provider): provider radio (Anthropic / OpenAI / Ollama Cloud) + key input. On submit, call `POST /api/ai/validate` (R2.1) and surface a typed error: `invalid_key` | `network_error` | `unsupported_provider`. Block advance until valid.
  - [ ] 2.4 Stage 3 (collection): single-line "Client / household name" input → `POST /api/collections` → set as current collection in `collectionStore`. Skippable with "I'll do this later".
  - [ ] 2.5 Stage 4 (done): one-screen confirmation; on dismiss, route to Chat tab with the new collection active.
  - [ ] 2.6 Persist completion: write `onboarding_completed_at` to app DB so the flow doesn't re-show after a refresh. Show a dismissible banner on the Chat tab if a user skipped provider setup (R1.7).
  - [ ] 2.7 Test: launch with empty `data/app.db` and confirm the 4 stages flow without console errors; quit at stage 2 and confirm the banner appears next launch.

- [ ] 3. R2 — Provider configuration in Settings
  - [ ] 3.1 In Basic Mode, [SettingsTab.vue](../../../frontend/src/components/SettingsTab.vue) shows only: provider selector, API key input, model dropdown for the selected provider, "Test connection" button, current-active badge.
  - [ ] 3.2 Wire the "Test connection" button to `validate_api_key` ([main.py:1834](../../../main.py#L1834)) and show success/failure within 10s with the same typed error vocabulary as 2.3.
  - [ ] 3.3 On chat-stream 401/403 from a stored provider, surface a non-blocking banner in ChatTab linking to Settings (R2.4). One banner only — don't stack.

- [ ] 4. R3 — Collection summary card
  - [ ] 4.1 Add a per-collection summary endpoint `GET /api/collections/{id}/summary` returning `{ positions, accounts, most_recent_export_iso, source_files: [...] }`. Pull from `HoldingsStore` (positions + distinct accounts) and document metadata (most recent CSV/XLSX `created_at`).
  - [ ] 4.2 Render the summary card in the Collection detail view above the document list. Hide silently when empty (no holdings yet).
  - [ ] 4.3 Confirm Pershing flat + NetX360 hierarchical files both populate the card correctly (use the existing fixtures under [tests/fixtures/](../../../tests/fixtures/)).

- [ ] 5. R4 — Meeting Brief promotion
  - [ ] 5.1 Add a "Generate Meeting Brief" primary button to the Collection detail view (visible whenever `summary.positions > 0`). The existing chat-tab quick-action chip stays — it's the in-chat shortcut.
  - [ ] 5.2 Render the brief response inline as a print-friendly layout with sections matching `services.brief_generator` output. Add a `@media print` stylesheet that drops nav/sidebars and forces single-column.
  - [ ] 5.3 Add an inline threshold strip (tax-loss min, concentration %, cash-drag min, top N) wired to the existing `/api/collections/{id}/brief` query params; debounce-regenerate on change.
  - [ ] 5.4 Footer the brief with the source filename(s) and `generated_at` so an advisor can verify currency (R4.4).
  - [ ] 5.5 When a section can't be computed (e.g. no cost basis → no tax-loss), show a labelled empty state rather than skipping silently (R4.5).

- [ ] 6. R5 — Chat polish for advisors
  - [ ] 6.1 Audit the chat agent toolset and confirm the five "no special syntax" question types resolve via existing tools: position lookup → `query_table`, account balance → `aggregate_table` per account, sector allocation → `compute_portfolio_metric` (or `aggregate_table`), unrealized P&L → `query_table` filtered, cash → `query_table` on cash sleeve. Add a fixture-backed test that runs each query against the Pershing snapshot.
  - [ ] 6.2 In Basic Mode, replace the `tool_start`/`tool_end` chips in [ChatTab.vue](../../../frontend/src/components/ChatTab.vue) with a single rolling "Thinking…" indicator. Expert Mode keeps the current detailed breadcrumb.
  - [ ] 6.3 When the chat lacks data to answer (no holdings, missing column role for the question), the engine must respond with an explicit "I don't have X for this collection" rather than guessing (R5.6). This is a prompt change in [services/chat/context.py](../../../services/chat/context.py) — add a hard rule to the system prompt.
  - [ ] 6.4 Clear `chatStore` history on `currentCollectionId` change (R5.8). Confirm via watcher in [chatStore.js](../../../frontend/src/stores/chatStore.js).

- [ ] 7. R6 — Surface meeting recording at the top level
  - [ ] 7.1 Promote the recording control from [SourcesSidebar.vue:610](../../../frontend/src/components/SourcesSidebar.vue#L610) to a header button (microphone icon) visible whenever a collection is active. Keep the sidebar entry as a duplicate for discoverability.
  - [ ] 7.2 Recording-active indicator: red pulsing dot in the header + tab favicon swap so an advisor can see the recording state from any tab.
  - [ ] 7.3 Confirm the existing audio upload path ([SourcesSidebar.vue:256](../../../frontend/src/components/SourcesSidebar.vue#L256)) covers MP3/M4A/WAV per R6.5; add a friendly error for unsupported formats (currently a generic upload error).

- [ ] 8. R7 — Note of Record drafting
  - [ ] 8.1 Add a "Draft Note of Record" button on the Collection detail view, visible whenever a `*.transcript.md` document exists in the collection.
  - [ ] 8.2 Wire the button to `/api/collections/{id}/notes/stream` ([main.py:3724](../../../main.py#L3724)) and stream the draft into an editable textarea — not into chat. The advisor edits, then "Save to collection" calls a new `POST /api/collections/{id}/notes/save` that writes a Markdown doc and indexes it.
  - [ ] 8.3 Render a redaction summary panel alongside the draft (entity-type counts) sourced from `RedactionLog.summarize` ([services/privacy/redaction_log.py:174](../../../services/privacy/redaction_log.py#L174)). Requires task 9.1 endpoints.

- [ ] 9. R9 — Privacy surfacing
  - [ ] 9.1 Add HTTP endpoints over `RedactionLog`:
    - `GET /api/redactions/summary?collection_id=&since=` → entity-type counts (R9.6)
    - `GET /api/redactions/log?collection_id=&limit=` → recent events (R9.3)
    - `POST /api/redactions/dry-run` body: `{ text, collection_id }` → returns `{ original, redacted, entities }` for the preview pane (R9.7)
  - [ ] 9.2 Add a persistent "PII redaction active" pill to the chat input bar that opens a slide-out viewer rendering the current session's events (R9.3, R9.4).
  - [ ] 9.3 Add a `Preview redactions` toggle to the Note of Record drafting view that calls `dry-run` before the AI request (R9.7). If preview is on, show the redacted text and an explicit "Send to AI" confirm.
  - [ ] 9.4 Stamp every Meeting Brief and Note of Record with a redaction-summary footer (R9.6).

- [ ] 10. R8 — Basic Mode hardening
  - [ ] 10.1 In [SettingsTab.vue](../../../frontend/src/components/SettingsTab.vue), wrap MCP, OCR, code-indexing, model-debug, redaction-tuning, and dev sections with `v-if="isExpertMode"`. Settings in Basic Mode shows: provider, mode toggle, app version. Nothing else.
  - [ ] 10.2 Add an error map: build a `friendlyError(err)` util that maps `502/503/timeout/decode_error/...` to plain-language strings + a suggested next action. Use it in the chat error toast and the upload error toast in Basic Mode (R8.7). Expert Mode keeps the raw message.
  - [ ] 10.3 Confirm `expertise` and `code-indexing` and `mcp` and `tokenizer` and `ocr-playground` tabs are hidden in Basic Mode (already filtered in [App.vue:1078](../../../frontend/src/App.vue#L1078); add tests).
  - [ ] 10.4 Expert-mode localStorage key migration is folded into task 11 (frontend rebrand sweep) — leave [expertMode.js](../../../frontend/src/utils/expertMode.js) alone here.

- [ ] 11. Frontend rebrand sweep — Phase 2 of Asymptote → Finn
  - **Why one task, not many:** half-migrated state on a pilot user's machine is worse than no migration. The Electron contextBridge name pairs with `window.asymptote.apiUrl` calls in the Vue source — renaming one without the other crashes the desktop app. Internal CustomEvent names must change emit-and-listen sites atomically. localStorage keys that lose their migration leave users with a reset chat history and "current collection." Do all of it in one PR with one verification pass.
  - **Out of scope here (already on Phase 1 ship list, tabled for desktop installer milestone):** PyInstaller spec, Inno Setup installer, `build_electron_*` scripts, `dist/Asymptote/` artifact directory, `electron/package.json` `productName`/`appId`/`shortcutName`/`dmg.title`/`extraResources` fields. Those are packaging, not runtime.
  - [ ] 11.1 Electron contextBridge: rename `contextBridge.exposeInMainWorld('asymptote', …)` → `'finn'` in [electron/preload.js](../../../electron/preload.js). Update both reads in [frontend/src/main.js:9-15](../../../frontend/src/main.js#L9) (`window.asymptote?.apiUrl` → `window.finn?.apiUrl`). Keep a backwards-compat read for one release: `window.finn?.apiUrl ?? window.asymptote?.apiUrl ?? ''`. Tag a follow-up to remove the fallback two releases later.
  - [ ] 11.2 CustomEvent rename: `'asymptote:prefill-chat'` → `'finn:prefill-chat'` and `'asymptote:providers-changed'` → `'finn:providers-changed'`. Update the dispatch sites at [App.vue:1204](../../../frontend/src/App.vue#L1204) and [App.vue:1258](../../../frontend/src/App.vue#L1258) **and** the listener sites at [ChatTab.vue:1320,1324](../../../frontend/src/components/ChatTab.vue#L1320) in the same edit. No backwards-compat shim — these are intra-app, atomic across the bundle.
  - [ ] 11.3 Build a single `migrateLegacyStorageKeys()` util in `frontend/src/utils/storage.js` that runs once on app boot (gated by a `finn_storage_migrated` sentinel). For each pair below, read old, write new, delete old:
    - `asymptote_expert_mode` → `finn_expert_mode`
    - `asymptote_chat_history_v2` → `finn_chat_history_v2` (and `asymptote_chat_history_v1` → `finn_chat_history_v1`)
    - `asymptote_current_collection` → `finn_current_collection`
    - `asymptote_default_top_k` → `finn_default_top_k`
    - `asymptote_search_cache_v2` → `finn_search_cache_v2`
    - `asymptote_search_settings_collapsed` → `finn_search_settings_collapsed`
    - `asymptote_selected_providers` → `finn_selected_providers`
    - `asymptote_search_model_overrides` → `finn_search_model_overrides`
    Call once from `frontend/src/main.js` before the Vue app mounts.
  - [ ] 11.4 Update every reader/writer of those keys to use the new constant names. Files: [expertMode.js](../../../frontend/src/utils/expertMode.js), [chatStore.js](../../../frontend/src/stores/chatStore.js), [collectionStore.js](../../../frontend/src/stores/collectionStore.js), [searchStore.js](../../../frontend/src/stores/searchStore.js), [SearchTab.vue](../../../frontend/src/components/SearchTab.vue).
  - [ ] 11.5 Production bundle refresh: `cd frontend && npm run build` to regenerate [static/assets/](../../../static/assets/) so users hitting `:8000` directly (not the Vite dev server) see the rebrand. Commit the new bundle.
  - [ ] 11.6 Verification: empty browser localStorage → load app → confirm migration sentinel is written and no `asymptote_*` keys remain. Then load app *with* the legacy keys pre-seeded → confirm chat history, current collection, and search prefs survive the migration. Then run `grep -rn "asymptote" frontend/src/ electron/main.js electron/preload.js` and confirm zero matches.

- [ ] 12. R10 — Pilot stability
  - [ ] 12.1 Add a top-level `<ErrorBoundary>` wrapper around tab content in [App.vue](../../../frontend/src/App.vue) showing a recovery card (Reload / Reset cache / Copy diagnostics) instead of a blank screen (R10.2).
  - [ ] 12.2 Add a `GET /api/version` endpoint returning `{ version, latest_known }` and a frontend banner that compares them; latest_known comes from a static file the desktop launcher writes at startup. No background polling.
  - [ ] 12.3 Persist `last_active_at` in the app DB on each `/health` ping; on launch, if `now - last_active_at >= 7 days`, show a one-screen welcome-back card with the three primary workflows (R10.7).
  - [ ] 12.4 Smoke check: empty data dir → onboarding → upload Pershing fixture → generate brief → record 30s of audio → draft Note of Record. Document the path in [desktop/DESKTOP_BUILD.md](../../../desktop/DESKTOP_BUILD.md).

- [ ] 13. Verification
  - [ ] 13.1 Manual e2e against a fresh `data/` directory using Pershing + NetX360 fixtures.
  - [ ] 13.2 Run `pytest` and confirm new tests for tasks 4.3, 6.1, 10.3 pass.
  - [ ] 13.3 Confirm zero `Asymptote` references in spec docs and runtime frontend (`grep -rni "asymptote" .kiro/specs/advisor-desktop-ux/ frontend/src/ electron/main.js electron/preload.js`). Packaging files (`build_electron_*`, `desktop/build_desktop.spec`, `desktop/installer.iss`, `dist/`) are still expected to match — they're on the packaging milestone, not this one.

## Notes

- **Task ordering rationale:** R1 + R2 + R3 are gating for the very first launch a pilot advisor sees. R4 (Brief) and R7 (Note of Record) are the demoable wins. R6 (recording) and R5 (chat polish) follow because they only matter once a collection has data. R8/R9 are the cross-cutting polish that benefits from having the surface stable first. §11 (frontend rebrand sweep) is intentionally **after** the visible-behavior tasks so it's not blocking pilot iteration; it can be picked up at any quiet point and shipped as one PR.
- **Out of pilot scope (defer):** auto-update mechanism beyond a banner; multi-machine sync; structured rules in expertise packs; advisor-shared collections; mobile/web-only deploys; the Electron/PyInstaller/Inno-Setup packaging rename (separate desktop installer milestone).
- **Budget signal:** if any single task in §2–§9 grows past three days, split it. The ones most likely to expand are 5.2 (print stylesheet) and 9.2 (PII viewer UX). §11 should be a single sitting — if migration design grows past one day it means the scope crept into packaging files and should be pushed back.

# Expertise Library — MVP Roadmap

**Status: ✅ SHIPPED** — committed to `csv-updates` on 2026-04-17.
**Scope:** Playbook-style expertise only. No formula engine, no PDF extraction, no versioning.

---

## The idea in one paragraph

Financial advisors have firm-wide knowledge that should shape every client analysis: investment policies, valuation frameworks, screening criteria, house views. Today Asymptote has no home for this — it all has to be retyped into every chat. The **Expertise Library** is a separate top-level area where advisors create reusable "expertise packs" (name + description + markdown body). On any client collection, the advisor ticks which packs apply, and those packs' text is injected into the chat system prompt so the AI automatically follows them when analyzing that client's portfolio.

Write a pack once. Apply it to many clients. Edit it in one place and every client benefits.

---

## MVP scope (in)

1. **Data model:** `expertise_packs` table + `collection_expertise` link table in the existing app DB.
2. **Backend CRUD:** create / read / update / delete expertise packs.
3. **Attach API:** set which packs apply to a given collection.
4. **Prompt injection:** when chatting on a collection, load attached packs and inject their bodies as system guidance in [main.py compose_prompt (~line 1249)](main.py#L1249).
5. **Frontend Library view:** list packs, create, edit (name / description / markdown body in a textarea), delete.
6. **Frontend Attach widget:** on the client collection view, a multi-select of available packs with checkboxes.
7. **Manual e2e test:** create one pack ("Conservative Income Policy"), attach it to a portfolio collection, ask a question, verify the AI's answer reflects the pack's guidance.

## Out of scope (defer to v2)

- Upload a PDF/DOCX and convert it into an expertise pack (extract text → pack body). *Advisors can copy-paste for MVP.*
- Structured rules and computational metrics (the "custom scoring" layer).
- Per-pack versioning, diff, rollback.
- Sharing / permissions / multi-user ownership.
- Semantic search *inside* the expertise library.
- Exposing expertise packs as MCP tools or resources.
- Rich markdown editor (textarea is fine for MVP).
- Tags, categories, folders for packs.

---

## Data model

Add to the existing app DB (same SQLite that stores MCP profiles).

```sql
CREATE TABLE IF NOT EXISTS expertise_packs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_expertise (
    collection_id TEXT NOT NULL,
    pack_id       TEXT NOT NULL,
    attached_at   TEXT NOT NULL,
    PRIMARY KEY (collection_id, pack_id),
    FOREIGN KEY (pack_id) REFERENCES expertise_packs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_collection_expertise_collection
    ON collection_expertise(collection_id);
```

Pydantic models in [models/schemas.py](models/schemas.py):

```python
class ExpertisePack(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    body: str
    created_at: datetime
    updated_at: datetime

class ExpertisePackCreate(BaseModel):
    name: str
    description: Optional[str] = None
    body: str

class ExpertisePackUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    body: Optional[str] = None
```

---

## Backend

### New file: `services/expertise_store.py`

Thin CRUD layer over the app DB. Functions:
- `list_packs() -> list[ExpertisePack]`
- `get_pack(pack_id) -> ExpertisePack | None`
- `create_pack(data: ExpertisePackCreate) -> ExpertisePack`
- `update_pack(pack_id, data: ExpertisePackUpdate) -> ExpertisePack`
- `delete_pack(pack_id) -> None`
- `get_packs_for_collection(collection_id) -> list[ExpertisePack]`
- `set_packs_for_collection(collection_id, pack_ids: list[str]) -> None`

### New endpoints in `main.py`

```
GET    /api/expertise/packs                      # list all packs
POST   /api/expertise/packs                      # create
GET    /api/expertise/packs/{pack_id}            # read one
PUT    /api/expertise/packs/{pack_id}            # update
DELETE /api/expertise/packs/{pack_id}            # delete

GET    /api/collections/{collection_id}/expertise   # list attached packs
PUT    /api/collections/{collection_id}/expertise   # set attached pack ids (array)
```

### Prompt injection

In [main.py](main.py) around [line 1249](main.py#L1249), inside `compose_prompt`:

1. Before the function, load attached packs once:
   ```python
   attached_packs = expertise_store.get_packs_for_collection(current_collection_id)
   ```
2. Inside `compose_prompt`, after `COLLECTION OVERVIEW`, insert:
   ```python
   if attached_packs:
       guidance = "\n\n".join(
           f"## {p.name}\n{p.description or ''}\n\n{p.body}".strip()
           for p in attached_packs
       )
       parts.extend([
           "",
           "ADVISOR EXPERTISE (apply these frameworks when analyzing this portfolio):\n"
           + guidance,
       ])
   ```
3. Add one line to `base_system_parts`:
   ```
   "When ADVISOR EXPERTISE is provided, follow its guidance, rules, and frameworks
    as authoritative instructions for this analysis."
   ```

Scope handling: for MVP, only inject expertise when `chat_request.scope` targets a single collection. If scope is `all`, skip injection (avoid pack conflicts across clients).

---

## Frontend

### New store: `frontend/src/stores/expertiseStore.js`

Pinia store mirroring the backend API. State: `packs`, `loading`, `error`. Actions: `fetchPacks`, `createPack`, `updatePack`, `deletePack`, `fetchAttached(collectionId)`, `setAttached(collectionId, packIds)`.

### New view: Expertise Library

Add a new top-level sidebar entry "Expertise" (parallel to Collections/Sources). Components:

- **`ExpertiseLibrary.vue`** — list of packs with name + description snippet, "New Pack" button, click to edit, delete button per row.
- **`ExpertisePackEditor.vue`** — form: name input, description input, body textarea (monospace, ~20 rows), Save / Cancel / Delete buttons. No markdown preview for MVP.

### Attach widget on the client collection view

In [frontend/src/components/SourcesSidebar.vue](frontend/src/components/SourcesSidebar.vue) (or wherever the collection's sidebar settings live), add a collapsible section "Applied Expertise":
- Lists currently attached packs with a remove (×) button each.
- "Add expertise…" dropdown shows unattached packs.
- Changes call `expertiseStore.setAttached(collectionId, packIds)` immediately.

---

## Implementation order (best path through the day)

1. **Schema + store** (`services/expertise_store.py`, table creation on startup) — 45 min
2. **Backend CRUD endpoints** (`main.py`) — 45 min
3. **Attach endpoints + prompt injection** — 45 min
4. **Smoke test backend with curl:** create a pack, attach it, chat, confirm it's in the prompt — 20 min
5. **Frontend store** (`expertiseStore.js`) — 30 min
6. **Library view + editor** (`ExpertiseLibrary.vue`, `ExpertisePackEditor.vue`, route/sidebar entry) — 2 hrs
7. **Attach widget** on collection sidebar — 1 hr
8. **End-to-end manual test in browser** — 30 min
9. **Fix whatever breaks** — buffer

Total: ~7 hours of focused work, which fits a day with overhead.

---

## Definition of done (MVP)

- [x] Advisor can open the Expertise Library, create a pack "Conservative Income Policy" with guidance text, and save it.
- [x] The pack appears in the library list and can be re-opened and edited.
- [x] On a portfolio collection, the advisor can attach that pack via the Applied Expertise section.
- [x] When the advisor asks "How does this portfolio align with our investment policy?", the AI's answer clearly reflects language and rules from the pack (not boilerplate).
- [x] Detaching the pack and asking the same question again produces a visibly different answer (proves injection is load-bearing).
- [x] Deleting a pack removes it from the library and from any collection it was attached to.

> **Empty state:** The library starts empty by design — no seed data. Advisors create their first pack via the "New Pack" button. The empty state UI shows a prompt to get started.

---

## Known MVP limitations to mention to the user after shipping

- Packs are plain markdown text — no structured rules, no formula execution.
- No upload-a-PDF-to-create-a-pack yet (copy-paste for now).
- Expertise is injected verbatim, so very long packs eat context budget. Keep packs focused.
- All users see all packs (no permissions model yet).
- Changing a pack mid-conversation doesn't retroactively update past messages.

These are all intentional deferrals — note them in the v2 scope section below when MVP ships.

---

## v2 candidates (after MVP lands and you see it in use)

- PDF/DOCX upload → extract → pre-fill pack body.
- Structured rule blocks inside a pack (name, condition, action) rendered as a form.
- Computational metrics defined declaratively (YAML or form-driven) that register with `compute_portfolio_metric`.
- Markdown preview in the editor.
- Pack categories / tags.
- "Suggested packs" based on collection contents.
- Expose packs as MCP resources so external agents can discover them.

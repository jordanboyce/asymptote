"""Chat and AI-provider endpoints."""

import logging
import asyncio
import time

from fastapi import Header, HTTPException, status, Request
from fastapi.responses import JSONResponse, StreamingResponse

from middleware.user_context import get_request_user
from services.usage_service import check_daily_budget, record_chat_usage

from services.ai_service import AIService, AnthropicProvider, create_provider
from services.agent_tools import anthropic_tools, openai_tools
from services.collection_overview import build_collection_overview as _build_collection_overview
from services.collection_service import collection_service
from services.chat_evidence import ChatEvidence, CITATION_INSTRUCTIONS
from services.research_search import RESEARCH_INSTRUCTIONS
from services.structured_chat import (
    _summarize_args as _summarize_for_log,
    build_structured_context,
    build_tool_use_instructions,
    collect_structured_tables,
    describe_tables_for_prompt,
    execute_tool_calls,
    format_results_for_prompt,
    parse_tool_calls,
    strip_tool_calls,
)
from models.schemas import (
    AIUsage,
    AIUsageDetail,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatSource,
)

from fastapi import APIRouter
from api.deps import (
    ALL_AI_PROVIDERS,
    CLOUD_AI_PROVIDERS,
    _truncate_tool_result,
    expertise_store,
    get_indexer,
    resolve_ai_key,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Semantic answer cache ───────────────────────────────────────────────────
# Repeated single-turn questions skip the provider entirely when a previous
# answer's question is close enough (local-embedding cosine similarity) and
# every source document that answer cited is unchanged. See
# services/answer_cache.py for the rules; these helpers own the pieces that
# need chat-layer context (indexers, document metadata, source URLs).

_FINGERPRINT_FIELDS = ("filename", "upload_timestamp", "num_chunks", "content_hash", "sensitivity")


def _selected_document_ids(chat_request: ChatRequest) -> list[str] | None:
    """The user's source selection, normalized; only meaningful for the
    current-collection scope (ids belong to one collection)."""
    ids = getattr(chat_request, "document_ids", None)
    if not ids or chat_request.scope == "all":
        return None
    cleaned = sorted({str(d).strip() for d in ids if str(d).strip()})
    return cleaned or None


def _requested_model(chat_request: ChatRequest, x_ai_model=None, x_ollama_model=None,
                     x_anthropic_model=None, x_openai_model=None, x_ai_base_url=None) -> str:
    """The model the request will actually run on, as the provider block
    resolves it — folded into the cache key so a switch of model or
    endpoint never serves the other one's answer."""
    if chat_request.provider == "ollama":
        model = x_ai_model or x_ollama_model or "llama3.2"
    else:
        model = x_ai_model or x_anthropic_model or x_openai_model or ""
    return f"{model}@{x_ai_base_url or ''}"


def _collection_guidance_version(collection_id: str) -> str:
    """Guide text + attached expertise, as a short digest.

    Both shape the system prompt, so a cached answer written under different
    guidance must not be reused after either changes."""
    import hashlib

    parts = []
    try:
        col = collection_service.get_collection(collection_id) or {}
        parts.append(col.get("guide") or "")
    except Exception:
        parts.append("")
    try:
        packs = expertise_store.get_packs_for_collection(collection_id)
        parts.extend(f"{p.id}:{p.updated_at}" for p in packs)
    except Exception:
        pass
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _corpus_version(collection_id: str) -> str:
    try:
        return get_indexer(collection_id).vector_store.metadata_store.corpus_version()
    except Exception:
        return "?"


def _request_fingerprint(chat_request: ChatRequest, collection_ids: list[str], model: str) -> str:
    """Everything besides the question that decides the answer.

    Provider + model + endpoint, retrieval settings, the source selection,
    and per collection the corpus version and guidance digest. Hashed into
    the scope key so a cached answer is only ever reused for a request that
    would have been built the same way — new uploads, a guide edit, an
    attached instruction pack, a model switch or a different selection all
    miss the cache instead of returning a stale answer.
    """
    import hashlib

    parts = [
        "evidence_protocol=3",
        f"provider={chat_request.provider}",
        f"model={model}",
        f"mode={getattr(chat_request.mode, 'value', chat_request.mode)}",
        f"top_k={chat_request.top_k}",
        f"rerank={int(bool(chat_request.rerank))}",
        "selection=" + ",".join(_selected_document_ids(chat_request) or []),
    ]
    for cid in collection_ids:
        parts.append(f"{cid}:corpus={_corpus_version(cid)}:guidance={_collection_guidance_version(cid)}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _cache_context(chat_request: ChatRequest, collection_id: str, model: str = ""):
    """Scope key + query embedding for this request, or None when the cache
    doesn't apply (disabled, or a multi-turn conversation — follow-ups depend
    on history a cached answer never saw).

    The scope key is ``<scope>#<request fingerprint>``: the scope names the
    collection(s), the fingerprint pins everything else that shapes the
    answer (see _request_fingerprint). Clearing a scope drops every
    fingerprint under it."""
    from config import settings

    if not settings.enable_answer_cache:
        return None
    if chat_request.messages[:-1]:
        return None

    try:
        if chat_request.scope == "all":
            # Visible collections define the scope: in private-collections
            # mode two users with different visibility never share entries.
            ids = sorted(c["id"] for c in collection_service.get_all_collections())
            if not ids:
                return None
            scope = "all:" + ",".join(ids)
            embed_cid = "default" if "default" in ids else ids[0]
        else:
            ids = [collection_id]
            scope = f"col:{collection_id}"
            embed_cid = collection_id

        scope_key = f"{scope}#{_request_fingerprint(chat_request, ids, model)}"
        embedder = get_indexer(embed_cid).embedding_service
        question = chat_request.messages[-1].content.strip()
        return {
            "scope_key": scope_key,
            "embedding_model": embedder.model_name,
            "vec": embedder.embed_query(question),
            "question": question,
        }
    except Exception as e:
        logger.debug(f"Answer cache unavailable for this request: {e}")
        return None


def _document_fingerprint(collection_id: str, document_id: str) -> dict | None:
    from services.governance import effective_sensitivity, is_hidden

    try:
        # Reauthorize on every cache hit: a share may have been revoked since
        # the question's scope was resolved. Never bypass the API access gate.
        info = get_indexer(collection_id).vector_store.metadata_store.get_document_info(document_id)
        if not info or is_hidden(info):
            return None
        collection = collection_service.get_collection(collection_id)
        fingerprint = {field: info.get(field) for field in _FINGERPRINT_FIELDS}
        fingerprint["sensitivity"] = effective_sensitivity(collection, info.get("sensitivity"))
        return fingerprint
    except Exception:
        return None


def _cache_lookup(cache_ctx):
    """Best fresh cached entry for this question, or None. Entries whose
    source documents changed or vanished are discarded on sight."""
    from config import settings
    from services.answer_cache import answer_cache

    entry = answer_cache.find_best(
        cache_ctx["scope_key"], cache_ctx["embedding_model"],
        cache_ctx["vec"], settings.answer_cache_threshold,
    )
    if entry is None:
        return None
    # Embeddings can place "2024 revenue" next to "2025 revenue", or
    # "is permitted" next to "is not permitted". Similarity is a candidate
    # lookup, not proof that a stored answer answers the same question.
    if " ".join(entry["question"].split()) != " ".join(cache_ctx["question"].split()):
        return None
    # Legacy or incomplete entries cannot establish the provenance of every
    # returned source, so regenerate rather than serving unvalidated text.
    source_ids = {(s.get("collection_id"), s.get("document_id")) for s in entry["sources"]}
    fingerprint_ids = {(fp.get("collection_id"), fp.get("document_id")) for fp in entry["fingerprints"]}
    if not source_ids or source_ids != fingerprint_ids:
        answer_cache.delete(entry["id"])
        return None
    for fp in entry["fingerprints"]:
        current = _document_fingerprint(fp["collection_id"], fp["document_id"])
        if current != {field: fp.get(field) for field in _FINGERPRINT_FIELDS}:
            answer_cache.delete(entry["id"])
            logger.info("Answer cache: stale or inaccessible source; entry discarded")
            return None
    answer_cache.mark_hit(entry["id"])
    logger.debug("Answer cache hit (similarity %s)", entry["similarity"])
    return entry


def _cache_store(cache_ctx, answer_text: str, filtered_results, provider: str):
    """Record a freshly generated answer. Answers without sources are never
    cached — 'nothing found' should stay retryable, and there would be no
    fingerprints to invalidate on."""
    from services.answer_cache import answer_cache

    if not answer_text or not filtered_results:
        return
    sources, fingerprints, seen = [], [], set()
    label_cache: dict = {}
    for r, cid in filtered_results:
        sources.append({
            "filename": r.filename,
            "page_number": r.page_number,
            "text_snippet": r.text_snippet,
            "similarity_score": r.similarity_score,
            "document_id": r.document_id,
            "collection_id": cid,
            "sensitivity": _source_sensitivity(cid, r, label_cache),
        })
        if (cid, r.document_id) not in seen:
            seen.add((cid, r.document_id))
            fp = _document_fingerprint(cid, r.document_id)
            if fp is None:
                return  # Do not persist an answer whose sources cannot be verified.
            fingerprints.append({"collection_id": cid, "document_id": r.document_id, **fp})
    try:
        answer_cache.store(
            cache_ctx["scope_key"], cache_ctx["embedding_model"],
            cache_ctx["question"], cache_ctx["vec"],
            answer_text, sources, fingerprints, provider,
        )
    except Exception as e:
        logger.warning(f"Answer cache store failed: {e}")


def _cached_sources(entry, base_url: str) -> list[dict]:
    """Rebuild source payloads with URLs for the current host."""
    return [
        {
            "filename": s["filename"],
            "page_number": s["page_number"],
            "text_snippet": s["text_snippet"],
            "similarity_score": s["similarity_score"],
            "document_id": s["document_id"],
            "pdf_url": f"{base_url}/documents/{s['document_id']}/pdf?collection_id={s['collection_id']}",
            "page_url": f"{base_url}/documents/{s['document_id']}/pdf?collection_id={s['collection_id']}#page={s['page_number']}",
            "sensitivity": s.get("sensitivity"),
        }
        for s in entry["sources"]
    ]


def _source_sensitivity(collection_id: str, result, _cache: dict) -> str:
    """Label in force for a retrieved chunk: document override, else the
    collection's. Collections are looked up once per turn."""
    from services.governance import effective_sensitivity

    if collection_id not in _cache:
        _cache[collection_id] = collection_service.get_collection(collection_id)
    return effective_sensitivity(_cache[collection_id], getattr(result, "sensitivity", None))


def _rerank_context(ai_service, query, results, collection_ids, top_k):
    """Keep passages and provenance paired for both chat transports."""
    if not results:
        return results, collection_ids, None
    try:
        ranked = ai_service.rerank_results(query, [
            {"index": i, "filename": r.filename, "text_snippet": r.text_snippet,
             "similarity_score": r.similarity_score}
            for i, r in enumerate(results)
        ], top_k)
        indices = list(dict.fromkeys(
            i for i in ranked["reranked_indices"]
            if type(i) is int and 0 <= i < len(results)
        ))
        if indices:
            return [results[i] for i in indices], [collection_ids[i] for i in indices], ranked.get("usage")
        return results, collection_ids, ranked.get("usage")
    except Exception as exc:
        logger.warning("Chat context reranking failed, using original order: %s", exc)
        return results, collection_ids, None


@router.delete("/api/chat/cache", tags=["chat"], summary="Clear the semantic answer cache")
async def clear_answer_cache(collection_id: str = None):
    """Drop cached answers — for one collection, or all of them."""
    from services.answer_cache import answer_cache
    from api.deps import require_admin, require_collection_access

    if collection_id:
        require_collection_access(collection_id, get_request_user(), write=True)
    else:
        require_admin("clear all cached answers")

    removed = answer_cache.clear(f"col:{collection_id}" if collection_id else None)
    return {"cleared": removed}


@router.post("/api/chat", response_model=ChatResponse)
def chat_with_documents(  # sync: provider round-trips + tool loop run in FastAPI's threadpool
    chat_request: ChatRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
) -> ChatResponse:
    """
    Chat with your indexed documents using conversational AI.

    Retrieves relevant document chunks based on the latest user message,
    then passes the context + conversation history to the selected AI provider.

    Supports:
    - scope='current': search only the specified collection
    - scope='all': search all collections and merge results by relevance
    - rerank=True: use AI to rerank retrieved context before generating response
    """
    turn_started = time.time()
    turn_user = get_request_user()
    usage_collection = "all" if chat_request.scope == "all" else collection_id
    try:
        # Extract the latest user message for context retrieval
        user_messages = [m for m in chat_request.messages if m.role == "user"]
        if not user_messages:
            raise HTTPException(status_code=400, detail="No user messages in conversation")

        latest_query = user_messages[-1].content

        # --- Semantic answer cache ---
        # Checked before provider construction: a cache hit needs no provider
        # (and no API key) at all. use_cache=false skips the lookup but still
        # stores the fresh answer, replacing the near-duplicate entry.
        cache_ctx = _cache_context(
            chat_request, collection_id,
            _requested_model(chat_request, x_ai_model, x_ollama_model, x_anthropic_model,
                             x_openai_model, x_ai_base_url),
        )
        if cache_ctx and chat_request.use_cache:
            cache_entry = _cache_lookup(cache_ctx)
            if cache_entry:
                record_chat_usage(
                    turn_user, usage_collection, chat_request.provider, "answer-cache",
                    input_tokens=0, output_tokens=0, cache_hit=True,
                    duration_ms=int((time.time() - turn_started) * 1000),
                )
                cached_base_url = str(request.base_url).rstrip("/")
                return ChatResponse(
                    message=ChatMessage(role="assistant", content=cache_entry["answer"]),
                    sources=[ChatSource(**s) for s in _cached_sources(cache_entry, cached_base_url)],
                    ai_usage=None,
                    cached=True,
                    cached_question=cache_entry["question"],
                )

        # --- Daily token budget ---
        # Checked only after the cache: a cache hit spends nothing, so it
        # stays available to a capped user.
        over_budget = check_daily_budget(turn_user)
        if over_budget:
            return JSONResponse(
                status_code=429,
                content=over_budget,
                headers={"Retry-After": str(over_budget["retry_after_seconds"])},
            )

        # --- Build AI provider (needed before search for query reformulation) ---
        try:
            if chat_request.provider == "ollama":
                model = x_ai_model or x_ollama_model or "llama3.2"
                extra: dict = {"model": model}
                if x_ai_base_url:
                    extra["base_url"] = x_ai_base_url
                provider = create_provider("ollama", **extra)
            else:
                x_ai_key = resolve_ai_key(chat_request.provider, x_ai_key)
                if not x_ai_key and chat_request.provider != "openai_compatible":
                    raise HTTPException(
                        status_code=400,
                        detail=f"API key required for {chat_request.provider} — pass X-AI-Key or store a team key via /api/agent/config",
                    )
                extra = {}
                model = x_ai_model or x_anthropic_model or x_openai_model
                if model:
                    extra["model"] = model
                if x_ai_base_url:
                    extra["base_url"] = x_ai_base_url
                provider = create_provider(chat_request.provider, x_ai_key, **extra)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to initialize AI provider: {str(e)}")

        ai_service = AIService(provider=provider)

        # --- Reformulate the query using conversation history ---
        # Follow-up questions ("what date was that?") reference prior context that the
        # vector index has no access to. Rewriting them into self-contained queries
        # (e.g. "Maverick Adventure First Stop receipt purchase date") dramatically
        # improves retrieval accuracy on multi-turn conversations. Skip on single-turn
        # queries to avoid an unnecessary LLM call (and the failure mode when the
        # provider is unreachable).
        prior_messages = chat_request.messages[:-1]
        if prior_messages:
            history_for_reformulation = [
                {"role": m.role, "content": m.content} for m in prior_messages
            ]
            search_query = ai_service.reformulate_query(history_for_reformulation, latest_query)
        else:
            search_query = latest_query

        # --- Retrieve context chunks ---
        # Track which collection each result came from so URLs are correct
        context_results = []
        result_collection_ids = []  # parallel list to context_results
        # The user's source selection constrains everything downstream:
        # retrieval here, the tables inlined below, the overview, and every
        # tool call the agent makes (services/structured_chat.py).
        selected_ids = _selected_document_ids(chat_request)
        selection_filters = {"document_ids": selected_ids} if selected_ids else None

        if chat_request.scope == "all":
            # Search every collection and merge results by similarity score
            all_collections = collection_service.get_all_collections()
            for col in all_collections:
                col_id = col["id"]
                try:
                    col_indexer = get_indexer(col_id)
                    col_search = col_indexer.search(
                        query=search_query,
                        top_k=chat_request.top_k,
                        mode=chat_request.mode,
                    )
                    for r in col_search["results"]:
                        context_results.append(r)
                        result_collection_ids.append(col_id)
                except Exception as e:
                    logger.warning(f"Chat: search failed for collection '{col_id}': {e}")

            # Sort merged results by similarity score, keep top_k
            paired = sorted(
                zip(context_results, result_collection_ids),
                key=lambda x: x[0].similarity_score,
                reverse=True,
            )[:chat_request.top_k]
            context_results = [p[0] for p in paired]
            result_collection_ids = [p[1] for p in paired]
        else:
            # Single collection
            try:
                indexer = get_indexer(collection_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

            search_result = indexer.search(
                query=search_query,
                top_k=chat_request.top_k,
                mode=chat_request.mode,
                filters=selection_filters,
            )
            context_results = search_result["results"]
            result_collection_ids = [collection_id] * len(context_results)

        # --- Optionally rerank context chunks ---
        rerank_usage = None
        if chat_request.rerank and context_results:
            context_results, result_collection_ids, rerank_usage = _rerank_context(
                ai_service, latest_query, context_results, result_collection_ids, chat_request.top_k
            )

        # --- Collect structured CSV/XLSX tables early so we can both inline
        # the small ones as JSONL AND know which files to drop from chunks. ---
        if chat_request.scope == "all":
            overview_ids = [c["id"] for c in collection_service.get_all_collections()]
        else:
            overview_ids = [collection_id]
        structured_tables, structured_stores = collect_structured_tables(overview_ids, selected_ids)
        structured_ctx = build_structured_context(structured_tables, structured_stores) \
            if structured_tables else {
                "inline_block": "",
                "tool_tables": [],
                "inlined_filenames": set(),
                "inlined_document_ids": set(),
            }
        inlined_filenames = structured_ctx["inlined_filenames"]
        tool_tables = structured_ctx["tool_tables"]
        inline_block = structured_ctx["inline_block"]

        # --- Format context and history ---
        # Drop chunks from files that are already fully inlined as JSONL: the
        # JSONL is authoritative, keeping duplicated (and possibly truncated)
        # chunk snippets would just confuse the model.
        filtered_results = [
            (r, cid) for (r, cid) in zip(context_results, result_collection_ids)
            if r.filename not in inlined_filenames
        ]
        context_parts = []
        for i, (result, _cid) in enumerate(filtered_results):
            context_parts.append(
                f"[Source {i + 1}: {result.filename}, page {result.page_number}]\n{result.text_snippet}"
            )
        context_text = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant context found."
        evidence = ChatEvidence(filtered_results)
        filtered_results = evidence.results

        history_parts = []
        for msg in chat_request.messages[:-1]:
            prefix = "User" if msg.role == "user" else "Assistant"
            history_parts.append(f"{prefix}: {msg.content}")
        history_text = "\n\n".join(history_parts) if history_parts else "(Start of conversation)"

        # --- Build collection overview (handles meta-questions like "how many files") ---
        try:
            collection_overview = _build_collection_overview(overview_ids, selected_ids)
        except Exception as e:
            logger.warning(f"Failed to build collection overview: {e}")
            collection_overview = "(Collection overview unavailable.)"

        scope_note = "all collections" if chat_request.scope == "all" else "the current collection"
        if selected_ids:
            scope_note = f"the {len(selected_ids)} source(s) the user selected in the current collection"

        # The agent always has the full toolkit available (search, table tools,
        # etc.). Only the LARGE-table schema block is conditional on tool_tables
        # — small tables are already inlined as JSONL.
        tables_block = describe_tables_for_prompt(tool_tables) if tool_tables else ""
        tools_block = build_tool_use_instructions()
        agent_context = {
            "collection_id": collection_id,
            "scope": chat_request.scope,
            "document_ids": selected_ids,
        }

        base_system_parts = [
            RESEARCH_INSTRUCTIONS,
            CITATION_INSTRUCTIONS,
            f"You are an analytical research assistant working over the user's "
            f"indexed sources in {scope_note}.",
            "Answer the user's question using the COLLECTION OVERVIEW, STRUCTURED "
            "TABLES (when provided), and RETRIEVED CONTEXT below.",
            "Ground every claim in the provided sources, cite them, and say so "
            "plainly when the sources don't contain the answer rather than guessing.",
            "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself "
            "(file counts, available documents, date ranges).",
        ]
        if inline_block:
            base_system_parts.append(
                "When STRUCTURED TABLES are included below, they are the FULL contents "
                "of CSV/XLSX files as JSONL — every row is present. For any numeric, "
                "sum, count, average, filter, date-range, or ranking question about "
                "those files, answer DIRECTLY from the JSONL rows and show your arithmetic. "
                "Do NOT guess from chunk snippets and do NOT assume data is missing."
            )
        if tool_tables:
            base_system_parts.append(
                "For questions about the LARGE tables listed under TOOL USE PROTOCOL, "
                "call the structured-query tools — do not estimate from row text."
            )
        base_system_parts.append(
            "Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] as needed."
        )

        # --- Expertise Library injection (single-collection scope only) ---
        # Load packs attached to this collection and build the guidance block.
        # Skipped for "all" scope to avoid conflicting guidance across clients.
        expertise_block: str = ""
        if chat_request.scope != "all":
            try:
                attached_packs = expertise_store.get_packs_for_collection(collection_id)
                if attached_packs:
                    base_system_parts.append(
                        "When EXPERTISE is provided, follow its guidance, rules, and "
                        "frameworks as authoritative instructions for this analysis."
                    )
                    guidance_sections = "\n\n".join(
                        f"## {p.name}\n{(p.description + chr(10)) if p.description else ''}{p.body}".strip()
                        for p in attached_packs
                    )
                    expertise_block = (
                        "EXPERTISE (apply these frameworks when analyzing these sources):\n"
                        + guidance_sections
                    )
            except Exception as _ep_err:
                logger.warning("Failed to load expertise packs for collection %s: %s", collection_id, _ep_err)

        base_system = " ".join(base_system_parts)

        def compose_prompt(extra_suffix: str = "") -> str:
            parts = [
                base_system,
                "",
                f"COLLECTION OVERVIEW:\n{collection_overview}",
            ]
            if expertise_block:
                parts.extend(["", expertise_block])
            if inline_block:
                parts.extend(["", inline_block])
            parts.extend(["", f"RETRIEVED CONTEXT:\n{context_text}"])
            if tables_block:
                parts.extend(["", f"LARGE TABLES (use SQL tool calls):\n{tables_block}"])
            if tools_block:
                parts.extend(["", tools_block])
            parts.extend([
                "",
                f"CONVERSATION HISTORY:\n{history_text}",
                "",
                f"User: {latest_query}",
            ])
            if extra_suffix:
                parts.append(extra_suffix)
            parts.append("Assistant:")
            return "\n\n".join(parts)

        # --- Agentic tool-use loop ---
        # When the provider supports native tool-calling (Anthropic, OpenAI
        # family) we drive the messages API directly — the model can't
        # respond with prose-only once it's decided to call a tool, which
        # eliminates the "let me query that for you" dead-end failure mode.
        # Ollama and other providers without native tools fall back to the
        # ReAct-over-prose path below.
        executed_results: list[dict] = []
        total_input_tokens = 0
        total_output_tokens = 0
        model_used = ai_service.quality_model
        response_text = ""
        max_iterations = 8

        # Build system prompt (no conversation history/user — those go into
        # messages natively) for the native-tools path.
        system_parts_native = [base_system, f"COLLECTION OVERVIEW:\n{collection_overview}"]
        if expertise_block:
            system_parts_native.append(expertise_block)
        if inline_block:
            system_parts_native.append(inline_block)
        if context_text and context_text != "No relevant context found.":
            system_parts_native.append(f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}")
        if tables_block:
            system_parts_native.append(f"LARGE TABLES AVAILABLE:\n{tables_block}")
        system_text_native = "\n\n".join(system_parts_native)

        if provider.supports_native_tools():
            is_anthropic = isinstance(provider, AnthropicProvider)
            tools_spec = anthropic_tools() if is_anthropic else openai_tools()

            # Seed messages with prior conversation + latest user query.
            messages: list[dict] = [
                {"role": m.role, "content": m.content}
                for m in chat_request.messages
            ]

            for iteration in range(max_iterations):
                logger.info("[agent] iter=%d provider=%s msgs=%d", iteration,
                            provider.__class__.__name__, len(messages))
                turn = provider.complete_with_tools(
                    messages=messages,
                    tools=tools_spec,
                    max_tokens=2048,
                    model=ai_service.quality_model,
                    system=system_text_native,
                )
                usage_iter = turn.get("usage", {}) or {}
                total_input_tokens += usage_iter.get("input_tokens", 0)
                total_output_tokens += usage_iter.get("output_tokens", 0)
                model_used = usage_iter.get("model", model_used)

                tool_calls = turn.get("tool_calls") or []
                thinking = turn.get("text") or ""
                logger.info(
                    "[agent] iter=%d stop=%s tool_calls=%d thinking=%r",
                    iteration, turn.get("stop_reason"), len(tool_calls),
                    thinking[:200] if thinking else "",
                )
                for tc in tool_calls:
                    logger.info("[agent]   -> %s %s", tc["name"], _summarize_for_log(tc.get("input") or {}))

                # Record any narration the model emitted alongside tool calls
                # so the UI can show the chain of thought.
                if tool_calls and thinking:
                    executed_results.append({
                        "tool": "_thinking",
                        "args": {"iteration": iteration},
                        "result": {"text": thinking},
                    })

                if not tool_calls:
                    response_text = thinking.strip()
                    break

                # Append the assistant turn verbatim (provider-native shape)
                # so the next call has the tool_use history.
                messages.append(turn["assistant_message"])

                # Execute each tool call and append tool results.
                adapted_calls = [
                    {"tool": tc["name"], **(tc.get("input") or {})}
                    for tc in tool_calls
                ]
                iter_results = execute_tool_calls(adapted_calls, agent_context=agent_context)
                evidence.observe(iter_results)
                executed_results.extend(iter_results)

                # Pair each call id with its result payload for the provider.
                import json as _json
                if is_anthropic:
                    tool_result_content = []
                    for tc, res in zip(tool_calls, iter_results):
                        payload = res.get("error") or res.get("result") or {}
                        tool_result_content.append({
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": _truncate_tool_result(_json.dumps(payload, default=str)),
                            **({"is_error": True} if res.get("error") else {}),
                        })
                    messages.append({"role": "user", "content": tool_result_content})
                else:
                    for tc, res in zip(tool_calls, iter_results):
                        payload = res.get("error") or res.get("result") or {}
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": _truncate_tool_result(_json.dumps(payload, default=str)),
                        })

            if not response_text:
                # Hit iteration cap without a clean final turn — force one
                # more pass with no tools so we always return an answer.
                try:
                    turn = provider.complete_with_tools(
                        messages=messages,
                        tools=[],
                        max_tokens=2048,
                        model=ai_service.quality_model,
                        system=system_text_native
                        + "\n\nDo not call any more tools. Summarize the final answer.",
                    )
                    response_text = (turn.get("text") or "").strip()
                    usage_iter = turn.get("usage", {}) or {}
                    total_input_tokens += usage_iter.get("input_tokens", 0)
                    total_output_tokens += usage_iter.get("output_tokens", 0)
                    model_used = usage_iter.get("model", model_used)
                except Exception as e:
                    logger.warning(f"Agent final-answer pass failed: {e}")
                    response_text = "I ran several tool calls but couldn't settle on a final answer — please rephrase or narrow the question."

        else:
            # --- ReAct fallback for providers without native tools (Ollama, etc.) ---
            suffix = ""
            for iteration in range(5):
                prompt = compose_prompt(suffix)
                result = provider.complete(prompt=prompt, max_tokens=2048, model=ai_service.quality_model)
                raw_text = result["text"]
                usage_iter = result.get("usage", {}) or {}
                total_input_tokens += usage_iter.get("input_tokens", 0)
                total_output_tokens += usage_iter.get("output_tokens", 0)
                model_used = usage_iter.get("model", model_used)

                calls = parse_tool_calls(raw_text)
                thinking = strip_tool_calls(raw_text).strip()
                if calls and thinking:
                    executed_results.append({
                        "tool": "_thinking",
                        "args": {"iteration": iteration},
                        "result": {"text": thinking},
                    })
                if not calls:
                    response_text = raw_text.strip()
                    break
                iter_results = execute_tool_calls(calls, agent_context=agent_context)
                evidence.observe(iter_results)
                executed_results.extend(iter_results)
                suffix = (
                    (suffix + "\n\n" if suffix else "")
                    + f"Assistant (previous turn):\n{raw_text.strip()}\n\n"
                    + format_results_for_prompt(iter_results)
                    + "\n\nYou may call more tools, or produce the final answer. "
                    + "When done, write the answer with no <tool_call> blocks."
                )
            response_text = strip_tool_calls(response_text).strip()
        usage = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "model": model_used,
        }

        features_used = ["chat"]
        if chat_request.rerank and rerank_usage:
            features_used.append("reranking")
        if executed_results:
            features_used.append("structured_tools")

        reranking_detail = None
        extra_input = 0
        extra_output = 0
        if rerank_usage:
            reranking_detail = AIUsageDetail(
                input_tokens=rerank_usage.get("input_tokens", 0),
                output_tokens=rerank_usage.get("output_tokens", 0),
                model=rerank_usage.get("model", ai_service.fast_model),
            )
            extra_input = rerank_usage.get("input_tokens", 0)
            extra_output = rerank_usage.get("output_tokens", 0)

        ai_usage = AIUsage(
            features_used=features_used,
            reranking=reranking_detail,
            synthesis=AIUsageDetail(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                model=usage.get("model", ai_service.quality_model),
            ),
            total_input_tokens=usage.get("input_tokens", 0) + extra_input,
            total_output_tokens=usage.get("output_tokens", 0) + extra_output,
        )

        # --- Build source list with URLs ---
        # Only show sources whose chunks actually made it into the prompt —
        # i.e. skip files that were inlined as authoritative JSONL.
        base_url = str(request.base_url).rstrip("/")
        label_cache: dict = {}
        sources = [
            ChatSource(
                filename=r.filename,
                page_number=r.page_number,
                text_snippet=r.text_snippet,
                similarity_score=r.similarity_score,
                document_id=r.document_id,
                pdf_url=f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}",
                page_url=f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}#page={r.page_number}",
                sensitivity=_source_sensitivity(col_id, r, label_cache),
            )
            for r, col_id in filtered_results
        ]

        if cache_ctx and not executed_results:
            _cache_store(cache_ctx, response_text, filtered_results, chat_request.provider)

        record_chat_usage(
            turn_user, usage_collection, chat_request.provider, model_used,
            input_tokens=ai_usage.total_input_tokens,
            output_tokens=ai_usage.total_output_tokens,
            tool_calls=sum(1 for r in executed_results if r.get("tool") != "_thinking"),
            duration_ms=int((time.time() - turn_started) * 1000),
        )

        return ChatResponse(
            message=ChatMessage(role="assistant", content=response_text),
            sources=sources,
            ai_usage=ai_usage,
            structured_results=executed_results or None,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Streaming chat endpoint (SSE)
# ---------------------------------------------------------------------------
# Mirrors /api/chat but emits Server-Sent Events so the frontend can display
# live tool-call indicators and stream the final text token-by-token.
#
# Event types (each line: "data: <json>\n\n"):
#   tool_start  — a tool call is about to execute
#   tool_end    — tool call finished (result included)
#   thinking    — prose the model emitted between tool calls
#   text_delta  — one word/chunk of the final response
#   sources     — final source list
#   done        — completion marker + usage stats
#   error       — something went wrong
@router.post("/api/chat/stream", tags=["chat"])
async def chat_stream_endpoint(
    chat_request: ChatRequest,
    request: Request,
    collection_id: str = "default",
    x_ai_key: str = Header(None),
    x_ollama_model: str = Header(None),
    x_anthropic_model: str = Header(None),
    x_openai_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Streaming version of /api/chat. Returns text/event-stream (SSE).

    Events are emitted as the agent works so the frontend can show live
    tool-call indicators and stream text as it arrives.
    """
    import json as _json

    turn_started = time.time()
    turn_user = get_request_user()
    usage_collection = "all" if chat_request.scope == "all" else collection_id

    # ---------- cache lookup + budget, before the stream starts ----------
    # The lookup runs out here for two reasons. It puts the cache before
    # provider construction, matching /api/chat (a cache hit needs no
    # provider or API key at all). And it lets the daily budget return a
    # real HTTP 429 — impossible once the SSE stream has begun — while a
    # budget-capped user keeps receiving cached answers, which cost nothing.
    cache_ctx = None
    cache_entry = None
    if any(m.role == "user" for m in chat_request.messages):
        try:
            cache_ctx = await asyncio.to_thread(
                _cache_context, chat_request, collection_id,
                _requested_model(chat_request, x_ai_model, x_ollama_model, x_anthropic_model,
                                 x_openai_model, x_ai_base_url),
            )
            if cache_ctx and chat_request.use_cache:
                cache_entry = await asyncio.to_thread(_cache_lookup, cache_ctx)
        except Exception as e:
            logger.warning(f"Answer cache lookup failed: {e}")
            cache_ctx = None
            cache_entry = None

    if cache_entry is None:
        over_budget = check_daily_budget(turn_user)
        if over_budget:
            return JSONResponse(
                status_code=429,
                content=over_budget,
                headers={"Retry-After": str(over_budget["retry_after_seconds"])},
            )

    async def generate():  # noqa: C901 (complexity fine for one function)
        try:
            # ---------- cached answer --------------------------------------------
            # Looked up before the stream began; no provider tokens spent.
            if cache_entry:
                record_chat_usage(
                    turn_user, usage_collection, chat_request.provider, "answer-cache",
                    input_tokens=0, output_tokens=0, cache_hit=True,
                    duration_ms=int((time.time() - turn_started) * 1000),
                )
                cached_base = str(request.base_url).rstrip("/")
                cached_sources = _cached_sources(cache_entry, cached_base)
                yield f"data: {_json.dumps({'type':'text_delta','delta':cache_entry['answer']})}\n\n"
                yield f"data: {_json.dumps({'type':'sources','sources':cached_sources})}\n\n"
                yield f"data: {_json.dumps({'type':'done','usage':{'input_tokens':0,'output_tokens':0,'model':'answer-cache'},'structured_results':[],'sources':cached_sources,'cached':True,'cached_question':cache_entry['question']})}\n\n"
                return

            # ---------- provider -------------------------------------------------
            try:
                if chat_request.provider == "ollama":
                    model_name = x_ai_model or x_ollama_model or "llama3.2"
                    extra: dict = {"model": model_name}
                    if x_ai_base_url:
                        extra["base_url"] = x_ai_base_url
                    provider = create_provider("ollama", **extra)
                else:
                    # NB: distinct name — assigning to x_ai_key here would shadow
                    # the enclosing endpoint parameter inside this generator.
                    resolved_key = resolve_ai_key(chat_request.provider, x_ai_key)
                    if not resolved_key and chat_request.provider != "openai_compatible":
                        yield f"data: {_json.dumps({'type':'error','message':f'API key required for {chat_request.provider}'})}\n\n"
                        return
                    extra = {}
                    model_name = x_ai_model or x_anthropic_model or x_openai_model
                    if model_name:
                        extra["model"] = model_name
                    if x_ai_base_url:
                        extra["base_url"] = x_ai_base_url
                    provider = create_provider(chat_request.provider, resolved_key, **extra)
            except Exception as e:
                yield f"data: {_json.dumps({'type':'error','message':f'Failed to initialize AI provider: {e}'})}\n\n"
                return

            ai_service = AIService(provider=provider)

            # ---------- context retrieval ----------------------------------------
            user_messages = [m for m in chat_request.messages if m.role == "user"]
            if not user_messages:
                yield f"data: {_json.dumps({'type':'error','message':'No user messages in conversation'})}\n\n"
                return
            latest_query = user_messages[-1].content

            # (semantic answer cache: looked up before the stream started —
            # see the cached-answer branch at the top of this generator)

            prior_messages = chat_request.messages[:-1]
            if prior_messages:
                history_for_reformulation = [
                    {"role": m.role, "content": m.content} for m in prior_messages
                ]
                # Blocking provider round-trip — keep it off the event loop
                search_query = await asyncio.to_thread(
                    ai_service.reformulate_query, history_for_reformulation, latest_query
                )
            else:
                search_query = latest_query

            context_results = []
            result_collection_ids = []
            selected_ids = _selected_document_ids(chat_request)
            selection_filters = {"document_ids": selected_ids} if selected_ids else None

            if chat_request.scope == "all":
                all_collections = collection_service.get_all_collections()
                for col in all_collections:
                    col_id = col["id"]
                    try:
                        col_indexer = get_indexer(col_id)
                        col_search = await asyncio.to_thread(
                            col_indexer.search,
                            query=search_query, top_k=chat_request.top_k, mode=chat_request.mode
                        )
                        for r in col_search["results"]:
                            context_results.append(r)
                            result_collection_ids.append(col_id)
                    except Exception as e:
                        logger.warning(f"Stream chat: search failed for collection '{col_id}': {e}")
                paired = sorted(
                    zip(context_results, result_collection_ids),
                    key=lambda x: x[0].similarity_score, reverse=True,
                )[:chat_request.top_k]
                context_results = [p[0] for p in paired]
                result_collection_ids = [p[1] for p in paired]
            else:
                try:
                    indexer = get_indexer(collection_id)
                except ValueError as e:
                    yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"
                    return
                sr = await asyncio.to_thread(
                    indexer.search, query=search_query, top_k=chat_request.top_k, mode=chat_request.mode,
                    filters=selection_filters,
                )
                context_results = sr["results"]
                result_collection_ids = [collection_id] * len(context_results)

            rerank_usage = None
            if chat_request.rerank:
                context_results, result_collection_ids, rerank_usage = await asyncio.to_thread(
                    _rerank_context, ai_service, latest_query, context_results,
                    result_collection_ids, chat_request.top_k,
                )

            # ---------- structured tables ----------------------------------------
            overview_ids = (
                [c["id"] for c in collection_service.get_all_collections()]
                if chat_request.scope == "all"
                else [collection_id]
            )
            structured_tables, structured_stores = await asyncio.to_thread(
                collect_structured_tables, overview_ids, selected_ids
            )
            structured_ctx = build_structured_context(structured_tables, structured_stores) if structured_tables else {
                "inline_block": "", "tool_tables": [], "inlined_filenames": set(), "inlined_document_ids": set()
            }
            inlined_filenames = structured_ctx["inlined_filenames"]
            tool_tables = structured_ctx["tool_tables"]
            inline_block = structured_ctx["inline_block"]

            filtered_results = [
                (r, cid) for (r, cid) in zip(context_results, result_collection_ids)
                if r.filename not in inlined_filenames
            ]
            context_text = "\n\n---\n\n".join(
                f"[Source {i+1}: {r.filename}, page {r.page_number}]\n{r.text_snippet}"
                for i, (r, _) in enumerate(filtered_results)
            ) or "No relevant context found."
            evidence = ChatEvidence(filtered_results)
            filtered_results = evidence.results

            try:
                collection_overview = await asyncio.to_thread(_build_collection_overview, overview_ids, selected_ids)
            except Exception as e:
                logger.warning(f"Stream chat: failed to build collection overview: {e}")
                collection_overview = "(Collection overview unavailable.)"

            tables_block = describe_tables_for_prompt(tool_tables) if tool_tables else ""

            # ---------- system prompt --------------------------------------------
            _scope_note = 'all collections' if chat_request.scope == 'all' else 'the current collection'
            if selected_ids:
                _scope_note = f"the {len(selected_ids)} source(s) the user selected in the current collection"
            base_system_parts = [
                RESEARCH_INSTRUCTIONS,
                CITATION_INSTRUCTIONS,
                f"You are an analytical research assistant working over the user's "
                f"indexed sources in {_scope_note}.",
                "Ground every claim in the provided sources, cite them, and say so "
                "plainly when the sources don't contain the answer rather than guessing.",
                "Answer the user's question using the COLLECTION OVERVIEW, STRUCTURED TABLES (when provided), and RETRIEVED CONTEXT below.",
                "Use the COLLECTION OVERVIEW for meta-questions about the knowledge base itself (file counts, available documents, date ranges).",
            ]
            if inline_block:
                base_system_parts.append(
                    "When STRUCTURED TABLES are included below, they are the FULL contents of CSV/XLSX files as JSONL — every row is present. "
                    "For any numeric, sum, count, average, filter, date-range, or ranking question about those files, answer DIRECTLY from the JSONL rows and show your arithmetic. "
                    "Do NOT guess from chunk snippets and do NOT assume data is missing."
                )
            if tool_tables:
                base_system_parts.append(
                    "For questions about the LARGE tables listed under TOOL USE PROTOCOL, call the structured-query tools — do not estimate from row text."
                )
            base_system_parts.append("Use RETRIEVED CONTEXT for prose/document questions, citing [Source N] as needed.")

            expertise_block = ""
            if chat_request.scope != "all":
                try:
                    attached_packs = expertise_store.get_packs_for_collection(collection_id)
                    if attached_packs:
                        base_system_parts.append(
                            "When EXPERTISE is provided, follow its guidance, rules, and frameworks as authoritative instructions for this analysis."
                        )
                        expertise_block = (
                            "EXPERTISE (apply these frameworks when analyzing these sources):\n"
                            + "\n\n".join(
                                f"## {p.name}\n{(p.description + chr(10)) if p.description else ''}{p.body}".strip()
                                for p in attached_packs
                            )
                        )
                except Exception as _ep:
                    logger.warning("Stream chat: failed to load expertise packs: %s", _ep)

            system_parts = [base_system_parts[0]]
            if len(base_system_parts) > 1:
                system_parts = base_system_parts
            system_text = "\n\n".join(
                [" ".join(base_system_parts),
                 f"COLLECTION OVERVIEW:\n{collection_overview}"]
                + ([expertise_block] if expertise_block else [])
                + ([inline_block] if inline_block else [])
                + ([f"PRE-RETRIEVED CONTEXT (optional primer):\n{context_text}"] if context_text and context_text != "No relevant context found." else [])
                + ([f"LARGE TABLES AVAILABLE:\n{tables_block}"] if tables_block else [])
            )

            # ---------- agent loop -----------------------------------------------
            agent_context = {"collection_id": collection_id, "scope": chat_request.scope,
                             "document_ids": selected_ids}
            executed_results: list[dict] = []
            total_input_tokens = (rerank_usage or {}).get("input_tokens", 0)
            total_output_tokens = (rerank_usage or {}).get("output_tokens", 0)
            model_used = ai_service.quality_model
            response_text = ""
            max_iterations = 8

            if provider.supports_native_tools():
                is_anthropic = isinstance(provider, AnthropicProvider)
                tools_spec = anthropic_tools() if is_anthropic else openai_tools()

                messages: list[dict] = [
                    {"role": m.role, "content": m.content} for m in chat_request.messages
                ]

                for iteration in range(max_iterations):
                    # Blocking provider round-trip — keep it off the event loop
                    turn = await asyncio.to_thread(
                        provider.complete_with_tools,
                        messages=messages,
                        tools=tools_spec,
                        max_tokens=4096,
                        model=ai_service.quality_model,
                        system=system_text,
                    )
                    usage_iter = turn.get("usage", {}) or {}
                    total_input_tokens += usage_iter.get("input_tokens", 0)
                    total_output_tokens += usage_iter.get("output_tokens", 0)
                    model_used = usage_iter.get("model", model_used)

                    tool_calls = turn.get("tool_calls") or []
                    thinking = turn.get("text") or ""

                    if not tool_calls:
                        response_text = thinking.strip()
                        break

                    # Emit any narration the model produced alongside tool calls
                    if thinking:
                        executed_results.append({"tool": "_thinking", "args": {"iteration": iteration}, "result": {"text": thinking}})
                        yield f"data: {_json.dumps({'type':'thinking','text':thinking})}\n\n"

                    # Emit tool_start for each call
                    for tc in tool_calls:
                        yield f"data: {_json.dumps({'type':'tool_start','tool':tc['name'],'args':tc.get('input') or {}})}\n\n"

                    messages.append(turn["assistant_message"])

                    # Execute all tool calls
                    adapted_calls = [{"tool": tc["name"], **(tc.get("input") or {})} for tc in tool_calls]
                    iter_results = await asyncio.to_thread(
                        execute_tool_calls, adapted_calls, agent_context=agent_context
                    )
                    evidence.observe(iter_results)
                    executed_results.extend(iter_results)

                    # Emit tool_end for each result
                    for tc, res in zip(tool_calls, iter_results):
                        yield f"data: {_json.dumps({'type':'tool_end','tool':tc['name'],'result':res})}\n\n"

                    # Append results to conversation
                    if is_anthropic:
                        tool_result_content = []
                        for tc, res in zip(tool_calls, iter_results):
                            payload = res.get("error") or res.get("result") or {}
                            tool_result_content.append({
                                "type": "tool_result",
                                "tool_use_id": tc["id"],
                                "content": _truncate_tool_result(_json.dumps(payload, default=str)),
                                **({"is_error": True} if res.get("error") else {}),
                            })
                        messages.append({"role": "user", "content": tool_result_content})
                    else:
                        for tc, res in zip(tool_calls, iter_results):
                            payload = res.get("error") or res.get("result") or {}
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": _truncate_tool_result(_json.dumps(payload, default=str)),
                            })

                if not response_text:
                    try:
                        turn = await asyncio.to_thread(
                            provider.complete_with_tools,
                            messages=messages, tools=[], max_tokens=2048,
                            model=ai_service.quality_model,
                            system=system_text + "\n\nDo not call any more tools. Summarize the final answer.",
                        )
                        response_text = (turn.get("text") or "").strip()
                        usage_iter = turn.get("usage", {}) or {}
                        total_input_tokens += usage_iter.get("input_tokens", 0)
                        total_output_tokens += usage_iter.get("output_tokens", 0)
                    except Exception as e:
                        logger.warning("Stream chat: final-answer pass failed: %s", e)
                        response_text = "I ran several tool calls but couldn't settle on a final answer — please rephrase or narrow the question."

            else:
                # ReAct fallback for Ollama — run synchronously then stream the text
                suffix = ""
                base_system = " ".join(base_system_parts)
                history_parts = [
                    f"{'User' if m.role == 'user' else 'Assistant'}: {m.content}"
                    for m in chat_request.messages[:-1]
                ]
                history_text = "\n\n".join(history_parts) if history_parts else "(Start of conversation)"
                tools_block = build_tool_use_instructions()

                def _compose(extra=""):
                    parts = [base_system, "", f"COLLECTION OVERVIEW:\n{collection_overview}"]
                    if expertise_block:
                        parts.extend(["", expertise_block])
                    if inline_block:
                        parts.extend(["", inline_block])
                    parts.extend(["", f"RETRIEVED CONTEXT:\n{context_text}"])
                    if tables_block:
                        parts.extend(["", f"LARGE TABLES (use SQL tool calls):\n{tables_block}"])
                    if tools_block:
                        parts.extend(["", f"TOOL USE PROTOCOL:\n{tools_block}"])
                    parts.extend(["", f"CONVERSATION HISTORY:\n{history_text}", "", f"User: {latest_query}"])
                    if extra:
                        parts.append(extra)
                    parts.append("Assistant:")
                    return "\n\n".join(parts)

                for iteration in range(5):
                    prompt = _compose(suffix)
                    result = await asyncio.to_thread(
                        provider.complete, prompt=prompt, max_tokens=2048, model=ai_service.quality_model
                    )
                    raw_text = result["text"]
                    usage_iter = result.get("usage", {}) or {}
                    total_input_tokens += usage_iter.get("input_tokens", 0)
                    total_output_tokens += usage_iter.get("output_tokens", 0)
                    model_used = usage_iter.get("model", model_used)

                    from services.structured_chat import parse_tool_calls
                    tool_calls_react = parse_tool_calls(raw_text)
                    if not tool_calls_react:
                        response_text = raw_text.strip()
                        break
                    for tc in tool_calls_react:
                        yield f"data: {_json.dumps({'type':'tool_start','tool':tc.get('tool','unknown'),'args':{}})}\n\n"
                    iter_results = await asyncio.to_thread(
                        execute_tool_calls, tool_calls_react, agent_context=agent_context
                    )
                    evidence.observe(iter_results)
                    executed_results.extend(iter_results)
                    for tc, res in zip(tool_calls_react, iter_results):
                        yield f"data: {_json.dumps({'type':'tool_end','tool':tc.get('tool','unknown'),'result':res})}\n\n"
                    result_text = _json.dumps([r.get("result") or r.get("error") for r in iter_results], default=str)
                    suffix += f"\nTool results: {result_text}\nContinue:"

            # ---------- stream the final text word-by-word -----------------------
            if response_text:
                words = response_text.split(" ")
                for i, word in enumerate(words):
                    chunk = word + (" " if i < len(words) - 1 else "")
                    yield f"data: {_json.dumps({'type':'text_delta','delta':chunk})}\n\n"
                    await asyncio.sleep(0.008)

            # ---------- sources --------------------------------------------------
            base_url = str(request.base_url).rstrip("/")
            label_cache: dict = {}
            sources_data = [
                {
                    "filename": r.filename,
                    "page_number": r.page_number,
                    "text_snippet": r.text_snippet,
                    "similarity_score": r.similarity_score,
                    "document_id": r.document_id,
                    "pdf_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}",
                    "page_url": f"{base_url}/documents/{r.document_id}/pdf?collection_id={col_id}#page={r.page_number}",
                    "sensitivity": _source_sensitivity(col_id, r, label_cache),
                }
                for r, col_id in filtered_results
            ]
            yield f"data: {_json.dumps({'type':'sources','sources':sources_data})}\n\n"

            if cache_ctx and not executed_results:
                await asyncio.to_thread(
                    _cache_store, cache_ctx, response_text, filtered_results, chat_request.provider
                )

            # ---------- done -----------------------------------------------------
            # sources ride the done event too: finalizeStreamingMessage commits
            # them from here (the standalone sources event predates that).
            record_chat_usage(
                turn_user, usage_collection, chat_request.provider, model_used,
                input_tokens=total_input_tokens, output_tokens=total_output_tokens,
                tool_calls=sum(1 for r in executed_results if r.get("tool") != "_thinking"),
                duration_ms=int((time.time() - turn_started) * 1000),
            )
            yield f"data: {_json.dumps({'type':'done','usage':{'input_tokens':total_input_tokens,'output_tokens':total_output_tokens,'model':model_used},'structured_results':executed_results,'sources':sources_data,'cached':False})}\n\n"

        except Exception as e:
            logger.exception("Stream chat failed")
            yield f"data: {_json.dumps({'type':'error','message':str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


# Local File Reference endpoints (v3.1 feature)

@router.post(
    "/api/ai/validate-key",
    summary="Validate an AI provider API key or Ollama model",
    tags=["ai"],
)
def validate_api_key(  # sync: provider round-trip runs in the threadpool
    x_ai_key: str = Header(None),
    x_ai_provider: str = Header("anthropic"),
    x_ollama_model: str = Header(None),
    x_ai_model: str = Header(None),
    x_ai_base_url: str = Header(None),
):
    """
    Validate an API key or Ollama model availability.

    For cloud providers (Anthropic, OpenAI):
    - Pass the key via X-AI-Key header
    - Pass provider via X-AI-Provider header

    For Ollama:
    - Pass 'ollama' via X-AI-Provider header
    - Pass model name via X-Ollama-Model header
    - X-AI-Key is not required
    """
    try:
        if x_ai_provider not in ALL_AI_PROVIDERS:
            return {
                "valid": False,
                "error": f"Invalid provider: {x_ai_provider}. "
                         f"Supported providers: {', '.join(ALL_AI_PROVIDERS)}"
            }

        if x_ai_provider == "ollama":
            model = x_ai_model or x_ollama_model or "llama3.2"
            extra: dict = {"model": model}
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            provider = create_provider("ollama", **extra)
        elif x_ai_provider == "openai_compatible":
            if not x_ai_base_url:
                return {"valid": False, "error": "base_url required for openai_compatible provider"}
            provider = create_provider(
                "openai_compatible",
                api_key=x_ai_key,
                base_url=x_ai_base_url,
                model=x_ai_model or "default",
            )
        else:
            if not x_ai_key:
                return {"valid": False, "error": "API key is required for cloud providers"}
            extra = {}
            if x_ai_model:
                extra["model"] = x_ai_model
            provider = create_provider(x_ai_provider, x_ai_key, **extra)

        valid = provider.validate()
        if not valid:
            # The provider's API rejected the key — say so explicitly, so a bad
            # key doesn't read as "the integration is broken".
            label = getattr(provider, "PROVIDER_LABEL", x_ai_provider)
            hint = getattr(provider, "KEY_HINT", "")
            msg = f"{label} rejected this API key."
            if hint:
                msg += f" Double-check it (or create a new one) at {hint}"
            return {"valid": False, "error": msg}
        return {"valid": True, "error": None}
    except Exception as e:
        error_str = str(e)
        logger.error(f"API key validation error for {x_ai_provider}: {e}")

        # Provide more helpful error messages
        if "quota" in error_str.lower() or "insufficient_quota" in error_str.lower():
            return {"valid": False, "error": "Your API key has exceeded its quota. Please add credits to your account."}
        elif "rate" in error_str.lower() and "limit" in error_str.lower():
            return {"valid": False, "error": "Rate limit exceeded. Please wait a moment and try again."}
        elif any(kw in error_str.lower() for kw in ("connection error", "connect error", "connection refused", "name or service not known", "failed to establish")):
            host = "ollama.com" if x_ai_provider == "ollama_cloud" else "the provider's API"
            return {"valid": False, "error": f"Could not reach {host}. Check your network connection and try again."}
        elif "model" in error_str.lower() and any(kw in error_str.lower() for kw in ("not found", "does not exist", "not_found", "unknown", "invalid model", "no access")):
            tried = x_ai_model or "the default model"
            return {"valid": False, "error": f"The key was accepted, but the provider rejected the model ({tried}). Pick a model your plan includes and test again."}
        else:
            return {"valid": False, "error": error_str}


@router.post(
    "/api/ai/models",
    summary="List available models for an AI provider",
    tags=["ai"],
)
def list_provider_models(  # sync: provider round-trip runs in the threadpool
    x_ai_key: str = Header(None),
    x_ai_provider: str = Header("anthropic"),
    x_ai_base_url: str = Header(None),
):
    """
    List the models an AI provider offers, using the caller's credentials.

    Same header contract as /api/ai/validate-key. Providers that cannot
    enumerate models return an empty list — the frontend then falls back to
    its curated recommendations. Missing X-AI-Key falls back to a
    server-stored team key for that provider, if one exists.
    """
    try:
        if x_ai_provider not in ALL_AI_PROVIDERS:
            return {"models": [], "error": f"Invalid provider: {x_ai_provider}"}

        api_key = x_ai_key
        if not api_key and x_ai_provider != "ollama":
            from services.app_database import app_db
            api_key = app_db.get_agent_api_key(x_ai_provider) or None

        if x_ai_provider == "ollama":
            extra: dict = {}
            if x_ai_base_url:
                extra["base_url"] = x_ai_base_url
            provider = create_provider("ollama", **extra)
        elif x_ai_provider == "openai_compatible":
            if not x_ai_base_url:
                return {"models": [], "error": "base_url required for openai_compatible provider"}
            provider = create_provider(
                "openai_compatible",
                api_key=api_key or "none",
                base_url=x_ai_base_url,
                model="default",
            )
        else:
            if not api_key:
                return {"models": [], "error": "API key is required for cloud providers"}
            provider = create_provider(x_ai_provider, api_key)

        return {"models": provider.list_models(), "error": None}
    except Exception as e:
        logger.warning(f"Model listing failed for {x_ai_provider}: {e}")
        return {"models": [], "error": str(e)}


@router.get(
    "/api/ai/deployment",
    summary="The AI provider this deployment configured for everyone",
    tags=["ai"],
)
async def get_deployment_ai_default():
    """
    What AI_PROVIDER / AI_BASE_URL / AI_MODEL describe: the model an operator
    wired into this deployment, so a browser that has configured nothing can
    still chat. The frontend lists it as an already-connected provider and
    sends no credentials for it — the server fills them in.

    Never returns AI_API_KEY, only whether one is on file. The base URL is
    returned: it is what the UI shows people so they can see which endpoint
    their questions go to, and on an on-prem install it is an internal
    hostname the caller is already inside the network of.
    """
    from config import settings

    provider = (settings.ai_provider or "").strip()
    if not provider:
        return {"configured": False, "offline_mode": settings.offline_mode}

    base_url = settings.ai_base_url or (
        settings.ollama_base_url if provider == "ollama" else ""
    )
    return {
        "configured": True,
        "provider": provider,
        "label": settings.ai_provider_label or _DEFAULT_PROVIDER_LABELS.get(provider, provider),
        "model": settings.ai_model,
        "base_url": base_url,
        "key_configured": bool(settings.ai_api_key),
        "offline_mode": settings.offline_mode,
    }


# Shown when AI_PROVIDER_LABEL is unset. "openai_compatible" is an interop
# shape, not a product name, so a private endpoint gets a neutral label
# rather than a vendor's.
_DEFAULT_PROVIDER_LABELS = {
    "openai_compatible": "Private endpoint",
    "ollama": "Ollama",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google Gemini",
    "grok": "Grok (xAI)",
    "github": "GitHub Models",
    "openrouter": "OpenRouter",
    "ollama_cloud": "Ollama Cloud",
}


@router.get(
    "/api/agent/config",
    summary="Get agent API configuration",
    tags=["agent"],
)
async def get_agent_config():
    """
    Get the current agent API configuration.

    Returns which providers have API keys configured (keys are masked for security).
    """
    from services.app_database import app_db
    return app_db.get_agent_config()


@router.post(
    "/api/agent/config",
    summary="Configure agent API key",
    tags=["agent"],
)
async def set_agent_config(
    provider: str,
    api_key: str,
):
    """
    Store an API key for agent use.

    This allows the /api/ask endpoint to work without requiring
    the X-AI-Key header on every request.

    Args:
        provider: AI provider name (anthropic, openai)
        api_key: The API key to store

    Note: Keys are stored server-side. For security, ensure your
    Clio instance is properly secured. A stored key becomes shared
    deployment state (everyone's chat can spend it), so under private
    collections this is admin-only — same trust rule as POST /api/config.
    """
    from api.deps import require_admin
    require_admin("store a team API key")
    from services.app_database import app_db

    if provider not in CLOUD_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}. Use one of {', '.join(CLOUD_AI_PROVIDERS)}.",
        )

    # Validate the key before storing
    try:
        test_provider = create_provider(provider, api_key)
        valid = test_provider.validate()
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"API key validation failed for {provider}",
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"API key validation failed: {e}",
        )

    app_db.set_agent_api_key(provider, api_key)
    return {"success": True, "message": f"API key configured for {provider}"}


@router.delete(
    "/api/agent/config/{provider}",
    summary="Remove agent API key",
    tags=["agent"],
)
async def delete_agent_config(provider: str):
    """
    Remove a stored API key. Admin-only under private collections —
    removing the team key breaks chat for everyone relying on it.

    Args:
        provider: AI provider name (anthropic, openai)
    """
    from api.deps import require_admin
    require_admin("remove the team API key")
    from services.app_database import app_db

    if provider not in CLOUD_AI_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}",
        )

    app_db.delete_agent_api_key(provider)
    return {"success": True, "message": f"API key removed for {provider}"}

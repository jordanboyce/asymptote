# Agentic document research

The in-app agent and MCP clients can use `research_documents` to investigate a
question through several searches, return a diverse set of passages, and see
which subquestions still lack selected evidence. `find_in_documents` complements
this with literal phrase and identifier lookup. Both feed the app's numbered
citations and evidence viewer.

## Try it

Restart the backend after updating and reconnect MCP clients to refresh their
tool inventory. For a repeatable demonstration, upload the fictional documents
described in [the agency pilot guide](AGENCY_PILOT.md), then ask:

> Research EQ-17. Compare the standard return rule with the field-team exception,
> check when each applies, and cite the supporting passages. Tell me what the
> documents do not establish.

Use a configured answer-generation model. The model chooses its tool calls;
the research instructions encourage focused subquestions and context verification.
Open **Research activity** to inspect returned passages, retrieval coverage, and
any failed steps. Click the answer's numbered citations to check its evidence.

An MCP client can call `research_documents` with arguments like:

```json
{
  "query": "Compare EQ-17 return rules and exceptions",
  "collection_id": "YOUR_COLLECTION_ID",
  "subqueries": [
    "EQ-17 standard equipment return period",
    "EQ-17 field-team exception and effective date"
  ],
  "top_k": 8,
  "max_per_document": 2,
  "max_context_chars": 12000
}
```

Use `filters: {"document_ids": ["YOUR_DOCUMENT_ID"]}` to narrow a call. The app
automatically enforces the user's selected documents on its agent calls; a tool
cannot widen that selection or switch collections while a selection is active.
Collection access checks still apply to every MCP request. Read-only annotations
describe tool behavior; authorization is enforced by the server.

For exact wording, call `find_in_documents` with `pattern`, `collection_id`, and
optional `filters`. The in-app agent uses literal matching. Then call
`get_document_context` using a returned `document_id` and `chunk_id` to examine
surrounding wording rather than relying on a short excerpt.

The same workflow fits development: search a configuration option, find its exact
identifier, and inspect the relevant implementation or runbook context.

## Retrieval behavior

- One original query plus up to three explicit subqueries; each is at most 1,000
  characters. Duplicate queries are removed without dropping negation.
- Hybrid retrieval for each query, keyword retrieval for the original question,
  and up to two literal searches for quoted phrases or identifiers such as EQ-17.
  A call performs at most seven retrieval branches, with no hidden model calls.
- Passages are deduplicated and combined using
  [reciprocal rank fusion](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf).
  Raw scores from different retrieval methods are not compared directly.
- Document diversity defaults to two passages per document. `top_k` accepts
  1–20 results and `max_per_document` accepts 1–10.
- The excerpt budget defaults to 12,000 characters and accepts 1,000–40,000.
  Each returned excerpt is capped at 2,000 characters. This bounds excerpt text,
  not the full JSON response, model tokens, corpus scan time, or end-to-end latency.
- Results include stable chunk identity, contributing queries and retrieval
  methods, a search trace, coverage, and a suggested context lookup. Identity is
  not a versioned snapshot or proof that the source content has not changed.
- Failed branches remain visible as failures. Other branches can still supply
  useful evidence; the app keeps partially failed research activity open.

Research currently operates within one collection per call. Source excerpts must
be enabled for the MCP profile. Numeric aggregation should continue to use the
structured table tools. Automatically inlined tables in ordinary document search
now respect document selection; inlining is skipped for other metadata filters
that the table collector cannot enforce.

## What coverage means

`retrieved` means selected passages came from a query's retrieval branches.
`no_selected_evidence` means none survived retrieval and selection for that query.
`search_failed` means all of that query's branches failed. These are retrieval
signals, not correctness scores or proof of a complete search. Context limits and
document diversity can omit candidates; the response reports the omitted count.

The agent is instructed to inspect dates and exceptions, rephrase missing
questions, avoid repeating successful searches, and report remaining gaps. This
does not guarantee claim verification. Evaluate the actual model and corpus with
the [pilot question set](demo/agency-evaluation.json) before making quality claims.

## Validation

Deterministic tests cover rank fusion, deduplication, diversity, budgets, failure
reporting, source filters, hidden documents, collection restrictions, citation
registration, and MCP tool discovery and result conversion. The scoped backend
suite passed 127 tests; the research UI and MCP panel passed six component tests.
The frontend production build passed. Lint reported no errors and five existing
`v-html` warnings.

These checks verify application behavior. Live model answer quality, customer
corpus recall, and deployment latency have not been benchmarked in this pass.

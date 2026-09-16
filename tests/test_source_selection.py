"""Selected sources are enforced end-to-end (docs/PRODUCT_ASSESSMENT.md):
initial retrieval, inlined tables, the overview, and every tool the agent can
call all stay inside the selection."""

from types import SimpleNamespace

import pytest

from models.schemas import ChatMessage, ChatRequest
from services import structured_chat
from services.structured_chat import _SourceSelection, collect_structured_tables, execute_tool_calls


class _FakeMcp:
    """Records what the tool executor asks for; answers with canned tables."""

    def __init__(self):
        self.calls = []
        self.tables = [
            {"table_name": "csv_a", "filename": "a.csv", "document_id": "docA"},
            {"table_name": "csv_b", "filename": "b.csv", "document_id": "docB"},
        ]

    def search_collection_sync(self, **kw):
        self.calls.append(("search", kw))
        return {"results": [], "filters": kw.get("filters")}

    def get_document_context(self, **kw):
        self.calls.append(("context", kw))
        return {"document_id": kw["document_id"]}

    def list_tables(self, collection_id=None):
        self.calls.append(("list_tables", collection_id))
        return {"collection_id": collection_id, "total_tables": 2, "tables": list(self.tables)}

    def get_table_schema(self, **kw):
        self.calls.append(("schema", kw))
        return {"ok": True}

    def get_table_rows(self, **kw):
        self.calls.append(("rows", kw))
        return {"ok": True}

    def query_table(self, **kw):
        self.calls.append(("sql", kw))
        return {"ok": True}

    def aggregate_table(self, **kw):
        self.calls.append(("agg", kw))
        return {"ok": True}

    def list_collections(self):
        return {"collections": []}

    def get_collection_info(self, collection_id=None):
        return {"id": collection_id}


@pytest.fixture()
def mcp(monkeypatch):
    fake = _FakeMcp()
    import sys
    import services

    # `from services import mcp_server` resolves the package attribute first
    # when the real module was already imported by an earlier test, so patch
    # both the attribute and the sys.modules entry.
    monkeypatch.setitem(sys.modules, "services.mcp_server", fake)
    monkeypatch.setattr(services, "mcp_server", fake, raising=False)
    return fake


CTX = {"collection_id": "default", "scope": "current", "document_ids": ["docA"]}


def _run(calls, ctx=CTX):
    return execute_tool_calls(calls, ctx)


# ── Tool loop ───────────────────────────────────────────────────────────


def test_search_is_narrowed_to_the_selection(mcp):
    out = _run([{"tool": "search_documents", "query": "q"}])
    assert "error" not in out[0]
    assert mcp.calls[-1][1]["filters"] == {"document_ids": ["docA"]}


def test_search_filters_from_the_model_are_intersected(mcp):
    out = _run([{"tool": "search_documents", "query": "q",
                 "filters": {"document_ids": ["docA", "docB"], "source_formats": ["pdf"]}}])
    assert "error" not in out[0]
    assert mcp.calls[-1][1]["filters"] == {"document_ids": ["docA"], "source_formats": ["pdf"]}

    out = _run([{"tool": "search_documents", "query": "q", "filters": {"document_ids": ["docB"]}}])
    assert "outside the sources" in out[0]["error"]


def test_document_context_refuses_unselected_ids(mcp):
    out = _run([{"tool": "get_document_context", "document_id": "docB"}])
    assert "not among the sources" in out[0]["error"]
    out = _run([{"tool": "get_document_context", "document_id": "docA"}])
    assert out[0]["result"] == {"document_id": "docA"}


def test_table_tools_are_limited_to_selected_sources(mcp):
    out = _run([{"tool": "list_tables"}])
    assert [t["table_name"] for t in out[0]["result"]["tables"]] == ["csv_a"]
    assert out[0]["result"]["total_tables"] == 1

    for tool in ("get_table_schema", "get_table_rows"):
        ok = _run([{"tool": tool, "identifier": "a.csv"}])
        assert "error" not in ok[0], ok
        refused = _run([{"tool": tool, "identifier": "csv_b"}])
        assert "outside the user's selection" in refused[0]["error"]

    refused = _run([{"tool": "aggregate_table", "identifier": "docB", "aggregate_col": "x", "agg_fn": "sum"}])
    assert "outside the user's selection" in refused[0]["error"]


def test_sql_naming_an_unselected_table_is_rejected(mcp):
    ok = _run([{"tool": "query_table", "sql": "SELECT COUNT(*) FROM csv_a"}])
    assert "error" not in ok[0]
    refused = _run([{"tool": "query_table", "sql": "SELECT * FROM csv_a JOIN csv_b USING (id)"}])
    assert "csv_b" in refused[0]["error"] and "Allowed tables: csv_a" in refused[0]["error"]


def test_no_selection_means_no_restriction(mcp):
    out = _run([{"tool": "search_documents", "query": "q"},
                {"tool": "get_document_context", "document_id": "docB"}],
               ctx={"collection_id": "default", "scope": "current", "document_ids": None})
    assert all("error" not in r for r in out)
    assert mcp.calls[0][1]["filters"] is None


def test_selection_loads_tables_once(mcp):
    sel = _SourceSelection(["docA"], "default", mcp)
    sel.require_table("a.csv")
    sel.require_sql("select 1 from csv_a")
    assert sum(1 for c in mcp.calls if c[0] == "list_tables") == 1


# ── Structured tables and the chat request ──────────────────────────────


def test_collect_structured_tables_filters_by_selection(monkeypatch):
    store = SimpleNamespace(list_tables=lambda: [
        {"table_name": "t1", "document_id": "docA"}, {"table_name": "t2", "document_id": "docB"},
    ])
    fake_indexer = SimpleNamespace(vector_store=SimpleNamespace(structured_store=store))
    import services.indexer_manager as im

    monkeypatch.setattr(im.indexer_manager, "get_indexer", lambda cid: fake_indexer)
    tables, stores = collect_structured_tables(["default"], ["docA"])
    assert [t["table_name"] for t in tables] == ["t1"]
    tables, _ = collect_structured_tables(["default"])
    assert len(tables) == 2


def test_chat_request_carries_document_ids():
    req = ChatRequest(messages=[ChatMessage(role="user", content="hi")], document_ids=["a", "b"])
    assert req.document_ids == ["a", "b"]
    from api.chat import _selected_document_ids

    assert _selected_document_ids(req) == ["a", "b"]
    # Ids belong to one collection: ignored under scope=all.
    req_all = ChatRequest(messages=[ChatMessage(role="user", content="hi")], document_ids=["a"], scope="all")
    assert _selected_document_ids(req_all) is None
    assert _selected_document_ids(ChatRequest(messages=[ChatMessage(role="user", content="hi")])) is None


def test_overview_describes_only_the_selection(monkeypatch):
    from services import collection_overview as co

    monkeypatch.setattr(co.collection_service, "get_collection", lambda cid: {"name": "Docs"})
    docs = [
        {"document_id": "docA", "filename": "a.pdf", "total_pages": 2, "total_chunks": 4, "upload_timestamp": "2026-01-01T00:00:00"},
        {"document_id": "docB", "filename": "b.pdf", "total_pages": 3, "total_chunks": 6, "upload_timestamp": "2026-02-01T00:00:00"},
    ]
    monkeypatch.setattr(co.indexer_manager, "get_indexer", lambda cid: SimpleNamespace(list_documents=lambda: docs))
    text = co.build_collection_overview(["default"], ["docA"])
    assert "Selected sources: 1 of 2" in text
    assert "a.pdf" in text and "b.pdf" not in text
    assert "Documents: 1, Pages: 2" in text
    full = co.build_collection_overview(["default"])
    assert "Selected sources" not in full and "b.pdf" in full

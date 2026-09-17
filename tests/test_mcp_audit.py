"""MCP tool-call auditing.

The write path has always been audited. Reads are what a regulated
deployment actually has to account for — "which credential read which
document, when" — and they were invisible until MCP_AUDIT_TOOL_CALLS. These
tests pin the two properties that make the trail worth keeping: it records
enough to answer that question, and it records nothing that would turn the
audit table into a second copy of the corpus.
"""

import pytest

import services.mcp_server as mcp
from services import audit
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service


@pytest.fixture()
def fresh_db(tmp_path):
    old_db_path = app_db.db_path
    old_base_dir = collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    yield app_db
    app_db.db_path = old_db_path
    collection_service.base_dir = old_base_dir


@pytest.fixture()
def auditing(fresh_db, monkeypatch):
    monkeypatch.setattr(mcp.settings, "mcp_audit_tool_calls", True)
    return fresh_db


# A search-shaped result, as the retrieval tools return one.
SEARCH_RESULT = {
    "collection_id": "research",
    "corpus_version": "v7",
    "results": [
        {"document_id": "doc-1", "filename": "budget-2027.xlsx", "page": 3,
         "text": "Line item 44 was cut in the second reading."},
        {"document_id": "doc-2", "filename": "minutes.pdf", "page": 11,
         "text": "The committee deferred the vote."},
    ],
}


def _events():
    return audit.list_events(action="mcp.tool_call")


def test_nothing_is_recorded_unless_the_setting_is_on(fresh_db, monkeypatch):
    monkeypatch.setattr(mcp.settings, "mcp_audit_tool_calls", False)
    mcp._tool_response(dict(SEARCH_RESULT), "search_collection")
    assert _events() == []


def test_a_read_records_tool_collection_and_documents(auditing):
    mcp._tool_response(dict(SEARCH_RESULT), "search_collection")

    events = _events()
    assert len(events) == 1
    event = events[0]
    assert event["target"] == "search_collection"
    assert event["collection_id"] == "research"
    assert event["detail"]["tool"] == "search_collection"
    assert event["detail"]["counts"]["results"] == 2
    assert event["detail"]["documents"] == ["budget-2027.xlsx", "minutes.pdf"]


def test_passage_text_never_reaches_the_audit_trail(auditing):
    """The trail is read by more people, and kept longer, than the corpus."""
    mcp._tool_response(dict(SEARCH_RESULT), "search_collection")

    blob = str(_events()[0]["detail"])
    assert "second reading" not in blob
    assert "deferred the vote" not in blob


def test_the_calling_token_is_named(auditing):
    token = mcp._request_mcp_token_id.set("tok-abc")
    identity = mcp._request_mcp_identity.set("analyst@agency.test")
    try:
        mcp._tool_response(dict(SEARCH_RESULT), "search_collection")
    finally:
        mcp._request_mcp_identity.reset(identity)
        mcp._request_mcp_token_id.reset(token)

    event = _events()[0]
    assert event["actor"] == "analyst@agency.test"
    assert event["detail"]["token_id"] == "tok-abc"


def test_document_list_is_bounded(auditing):
    """A wide research call can touch hundreds of sources; one row stays readable."""
    wide = {
        "collection_id": "research",
        "results": [{"document_id": f"doc-{i}", "filename": f"f{i}.pdf"} for i in range(60)],
    }
    mcp._tool_response(wide, "research_documents")

    detail = _events()[0]["detail"]
    assert len(detail["documents"]) == 25
    assert detail["documents_truncated"] == 35
    assert detail["counts"]["results"] == 60


def test_cross_collection_results_record_every_collection(auditing):
    mcp._tool_response(
        {"collection_ids": ["research", "legal"], "results": []},
        "search_all_collections",
    )
    assert _events()[0]["detail"]["collection_ids"] == ["research", "legal"]


def test_a_single_document_read_is_attributed_to_that_document(auditing):
    mcp._tool_response(
        {"collection_id": "research", "document_id": "doc-1",
         "filename": "budget-2027.xlsx", "text": "secret contents"},
        "get_document_context",
    )
    event = _events()[0]
    assert event["document_id"] == "doc-1"
    assert "secret contents" not in str(event["detail"])


def test_the_response_is_returned_unchanged(auditing):
    payload = dict(SEARCH_RESULT)
    assert mcp._tool_response(payload, "search_collection") is payload


def test_an_audit_failure_never_fails_the_tool_call(auditing, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(audit, "record", boom)
    payload = dict(SEARCH_RESULT)
    assert mcp._tool_response(payload, "search_collection") is payload

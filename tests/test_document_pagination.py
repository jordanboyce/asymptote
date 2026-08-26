"""SQL-side document pagination for large collections (sidebar scaling)."""

from pathlib import Path

import pytest

from services.metadata_store import MetadataStore


@pytest.fixture()
def store(tmp_path):
    s = MetadataStore(tmp_path / "metadata.db")
    for i in range(25):
        s.add_document(
            document_id=f"doc{i:03d}",
            filename=f"report-{i:03d}.html" if i % 5 else f"summary-{i:03d}.html",
            num_pages=1,
            num_chunks=3,
            upload_timestamp=f"2026-01-{(i % 28) + 1:02d}T00:00:0{i % 10}",
        )
    return s


def test_count_all(store):
    assert store.count_documents() == 25


def test_count_filtered(store):
    assert store.count_documents(q="summary") == 5
    assert store.count_documents(q="report") == 20
    assert store.count_documents(q="nope") == 0


def test_page_size_and_disjoint_pages(store):
    page1 = store.list_documents_page(10, offset=0)
    page2 = store.list_documents_page(10, offset=10)
    page3 = store.list_documents_page(10, offset=20)
    assert len(page1) == 10 and len(page2) == 10 and len(page3) == 5
    ids = [d["document_id"] for d in page1 + page2 + page3]
    assert len(set(ids)) == 25


def test_page_ordering_newest_first(store):
    page = store.list_documents_page(25)
    stamps = [d["upload_timestamp"] for d in page]
    assert stamps == sorted(stamps, reverse=True)


def test_page_filter(store):
    page = store.list_documents_page(50, q="summary")
    assert len(page) == 5
    assert all("summary" in d["filename"] for d in page)


def test_filter_escapes_like_wildcards(store):
    # '%' and '_' must be literals in the query, not wildcards.
    assert store.count_documents(q="%") == 0
    assert store.count_documents(q="_") == 0


def test_page_row_shape_matches_full_list(store):
    full = store.list_documents()[0]
    paged = store.list_documents_page(1)[0]
    assert set(paged.keys()) == set(full.keys())

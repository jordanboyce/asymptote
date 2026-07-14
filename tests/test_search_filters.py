"""Tests for metadata search filters (Feature: query-time filtering).

Exercises MetadataStore.get_filtered_chunk_ids / get_filter_facets directly —
pure SQLite, no embeddings or FAISS required.
"""

import pytest

from services.metadata_store import MetadataStore


@pytest.fixture
def store(tmp_path):
    s = MetadataStore(tmp_path / "meta.db")
    # Two PDFs and one CSV, with distinct upload timestamps.
    s.add_document("d_pdf1", "alpha.pdf", num_pages=1, num_chunks=2,
                   upload_timestamp="2026-01-10T00:00:00", source_format="pdf")
    s.add_document("d_pdf2", "beta.pdf", num_pages=1, num_chunks=1,
                   upload_timestamp="2026-03-20T00:00:00", source_format="pdf")
    s.add_document("d_csv1", "data.csv", num_pages=1, num_chunks=1,
                   upload_timestamp="2026-06-01T00:00:00", source_format="csv")
    s.add_chunks([
        {"chunk_id": "a1", "document_id": "d_pdf1", "filename": "alpha.pdf",
         "page_number": 1, "chunk_index": 0, "text": "x", "source_format": "pdf"},
        {"chunk_id": "a2", "document_id": "d_pdf1", "filename": "alpha.pdf",
         "page_number": 1, "chunk_index": 1, "text": "y", "source_format": "pdf"},
        {"chunk_id": "b1", "document_id": "d_pdf2", "filename": "beta.pdf",
         "page_number": 1, "chunk_index": 0, "text": "z", "source_format": "pdf"},
        {"chunk_id": "c1", "document_id": "d_csv1", "filename": "data.csv",
         "page_number": 1, "chunk_index": 0, "text": "w", "source_format": "csv"},
    ])
    return s


def test_no_filters_returns_none(store):
    assert store.get_filtered_chunk_ids() is None


def test_filter_by_document_id(store):
    assert store.get_filtered_chunk_ids(document_ids=["d_pdf1"]) == {"a1", "a2"}


def test_filter_by_source_format_case_and_dot_insensitive(store):
    assert store.get_filtered_chunk_ids(source_formats=["PDF"]) == {"a1", "a2", "b1"}
    assert store.get_filtered_chunk_ids(source_formats=[".csv"]) == {"c1"}


def test_filter_by_filename_case_insensitive(store):
    assert store.get_filtered_chunk_ids(filenames=["ALPHA.PDF"]) == {"a1", "a2"}


def test_filter_by_date_range(store):
    # Only beta.pdf falls inside this window.
    got = store.get_filtered_chunk_ids(
        date_from="2026-02-01T00:00:00", date_to="2026-04-01T00:00:00"
    )
    assert got == {"b1"}


def test_filters_compose_with_and(store):
    # pdf AND on/after March -> only beta.pdf
    got = store.get_filtered_chunk_ids(
        source_formats=["pdf"], date_from="2026-02-01T00:00:00"
    )
    assert got == {"b1"}


def test_filter_matching_nothing_returns_empty_set_not_none(store):
    got = store.get_filtered_chunk_ids(document_ids=["does-not-exist"])
    assert got == set()
    assert got is not None  # empty set => "nothing matches", distinct from no-filter


def test_facets_lists_formats_and_documents(store):
    facets = store.get_filter_facets()
    assert set(facets["source_formats"]) == {"pdf", "csv"}
    assert len(facets["documents"]) == 3
    names = {d["filename"] for d in facets["documents"]}
    assert names == {"alpha.pdf", "beta.pdf", "data.csv"}

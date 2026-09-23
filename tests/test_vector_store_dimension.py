"""A dimension change is refused everywhere except by a re-index."""

import pytest

from services.vector_store import VectorStore


def test_mismatched_index_is_refused_by_default(tmp_path):
    VectorStore(tmp_path, embedding_dim=384).save()
    with pytest.raises(RuntimeError, match="has dimension 384"):
        VectorStore(tmp_path, embedding_dim=2048)


def test_reindex_may_discard_a_mismatched_index(tmp_path):
    VectorStore(tmp_path, embedding_dim=384).save()
    store = VectorStore(tmp_path, embedding_dim=2048, discard_mismatched_index=True)
    assert store.index.d == 2048
    store.clear_index()
    assert VectorStore(tmp_path, embedding_dim=2048).index.d == 2048

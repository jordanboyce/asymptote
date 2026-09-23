"""A running re-index must not freeze the event loop.

The job bodies are synchronous end to end (extraction, vision OCR over the
network, embedding, save). They used to run as a bare coroutine, so a long
OCR job hung every request - health checks and settings saves included -
until it finished.
"""

import asyncio
import time
from pathlib import Path

from services import reindex_service as rs


def test_collection_reindex_runs_off_the_event_loop(monkeypatch):
    monkeypatch.setattr(rs.app_db, "get_active_reindex_job", lambda: None)
    monkeypatch.setattr(rs.app_db, "create_reindex_job", lambda snapshot: 1)

    service = rs.ReindexService()
    body_ran = []

    def slow_body(**kwargs):
        time.sleep(1.0)  # stands in for a long, blocking OCR job
        body_ran.append(kwargs["collection_id"])

    monkeypatch.setattr(service, "_run_collection_reindex", slow_body)

    async def scenario():
        await service.start_collection_reindex(
            collection_id="c1",
            documents_dir=Path("."),
            indexes_dir=Path("."),
            embedding_model="m",
            chunk_size=100,
            chunk_overlap=10,
        )
        # While the job runs, the loop should still tick promptly.
        t0 = time.perf_counter()
        await asyncio.sleep(0.05)
        loop_delay = time.perf_counter() - t0
        await asyncio.gather(*service._tasks)
        return loop_delay

    loop_delay = asyncio.run(scenario())
    assert loop_delay < 0.5
    assert body_ran == ["c1"]


class _FakeMetadataStore:
    def __init__(self, docs):
        self._docs = docs

    def list_documents(self):
        return self._docs


def test_missing_source_files_stop_the_reindex_before_it_clears(tmp_path):
    present = tmp_path / "here.pdf"
    present.write_bytes(b"x")
    store = _FakeMetadataStore([
        {"document_id": "a", "filename": "here.pdf"},
        {"document_id": "b", "filename": "gone.pdf",
         "source_type": "local_reference", "source_path": str(tmp_path / "elsewhere" / "gone.pdf")},
    ])

    import pytest
    with pytest.raises(RuntimeError, match="gone.pdf"):
        rs.ReindexService()._get_documents_to_reindex(store, tmp_path)


def test_all_sources_present_are_listed(tmp_path):
    (tmp_path / "here.pdf").write_bytes(b"x")
    store = _FakeMetadataStore([{"document_id": "a", "filename": "here.pdf"}])
    docs = rs.ReindexService()._get_documents_to_reindex(store, tmp_path)
    assert [d["document_id"] for d in docs] == ["a"]

"""Indexing job lifecycle: queueing, cancellation, and restart recovery.

The bulk pipeline itself is covered in tests/test_bulk_indexing.py. What
is exercised here is everything around it — the parts a person actually
experiences as "indexing is unreliable": a second job refused instead of
queued, a job that shows as running forever because the process that owned
it is gone, a Cancel button with nothing behind it, and a finished job
still reporting the file it was working on.
"""

import threading
import time

import pytest

from services.app_database import app_db, SQLiteBackend
from services.upload_service import UploadService, _QueuedJob


@pytest.fixture
def jobs_db(tmp_path):
    """An empty app database for job rows."""
    old_db_path = app_db.db_path
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    yield app_db
    app_db.db_path = old_db_path


@pytest.fixture
def service():
    """A service of its own, so queue state never leaks between tests."""
    return UploadService()


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ── Restart recovery ────────────────────────────────────────────────────


def test_interrupted_jobs_are_failed_not_left_running(jobs_db, service):
    """A restart closes out jobs whose threads died with the old process."""
    running = jobs_db.create_upload_job("c1", 10, job_type="index")
    jobs_db.update_upload_job(running, status="running", current_file="half-done.pdf")
    waiting = jobs_db.create_upload_job("c2", 3, job_type="upload")  # left 'pending'
    done = jobs_db.create_upload_job("c3", 1, job_type="index")
    jobs_db.update_upload_job(done, status="completed")

    counts = service.recover_orphaned_jobs()

    assert counts["upload_jobs"] == 2
    for job_id in (running, waiting):
        row = jobs_db.get_upload_job(job_id)
        assert row["status"] == "failed"
        assert "restarted" in row["error"]
        assert row["current_file"] is None
        assert row["completed_at"]
    # A job that had already finished is left exactly as it was.
    assert jobs_db.get_upload_job(done)["status"] == "completed"


def test_recovery_frees_the_collection_for_new_jobs(jobs_db, service):
    """The stale row no longer blocks the collection it was indexing into."""
    stale = jobs_db.create_upload_job("c1", 10, job_type="index")
    jobs_db.update_upload_job(stale, status="running")
    assert jobs_db.get_active_upload_job("c1") is not None

    service.recover_orphaned_jobs()

    assert jobs_db.get_active_upload_job("c1") is None
    assert jobs_db.get_all_active_upload_jobs() == []


# ── Queueing ────────────────────────────────────────────────────────────


def test_second_job_for_a_collection_waits_instead_of_being_refused(service):
    """Two jobs for one collection run one after the other, both accepted."""
    order = []
    first_may_finish = threading.Event()

    def first():
        order.append("first-start")
        first_may_finish.wait(timeout=5)
        order.append("first-end")

    def second():
        order.append("second-start")

    service._submit(_QueuedJob(1, "c1", first, ()))
    assert _wait_for(lambda: "first-start" in order)

    service._submit(_QueuedJob(2, "c1", second, ()))
    # Same collection is busy, so the second job waits its turn.
    assert service.queue_position(2) == 1
    assert "second-start" not in order

    first_may_finish.set()
    assert _wait_for(lambda: "second-start" in order)
    assert order == ["first-start", "first-end", "second-start"]
    assert service.queue_position(2) is None


def test_a_busy_collection_does_not_block_other_collections(service):
    """A job for another collection starts while the head of the queue waits."""
    started = []
    hold = threading.Event()

    def blocker():
        started.append("blocker")
        hold.wait(timeout=5)

    service._submit(_QueuedJob(1, "c1", blocker, ()))
    assert _wait_for(lambda: "blocker" in started)

    service._submit(_QueuedJob(2, "c1", lambda: started.append("same-collection"), ()))
    service._submit(_QueuedJob(3, "c2", lambda: started.append("other-collection"), ()))

    assert _wait_for(lambda: "other-collection" in started)
    assert "same-collection" not in started
    hold.set()
    assert _wait_for(lambda: "same-collection" in started)


def test_a_job_that_crashes_releases_its_collection(jobs_db, service):
    """A target blowing up must not strand the collection forever."""
    def explode():
        raise RuntimeError("extraction exploded")

    job_id = jobs_db.create_upload_job("c1", 1, job_type="index")
    service._submit(_QueuedJob(job_id, "c1", explode, ()))
    assert _wait_for(lambda: service.active_job_count() == 0)

    row = jobs_db.get_upload_job(job_id)
    assert row["status"] == "failed"
    assert "extraction exploded" in row["error"]

    ran = []
    service._submit(_QueuedJob(job_id + 1, "c1", lambda: ran.append(1), ()))
    assert _wait_for(lambda: ran == [1])


# ── Cancellation ────────────────────────────────────────────────────────


def test_cancelling_a_queued_job_drops_it_before_it_runs(jobs_db, service):
    """A job still in line is cancelled outright, never started."""
    hold = threading.Event()
    ran = []
    blocker_id = jobs_db.create_upload_job("c1", 1, job_type="index")
    queued_id = jobs_db.create_upload_job("c1", 1, job_type="index")

    service._submit(_QueuedJob(blocker_id, "c1", lambda: hold.wait(timeout=5), ()))
    assert _wait_for(lambda: service.active_job_count() == 1)
    service._submit(_QueuedJob(queued_id, "c1", lambda: ran.append(1), ()))

    assert service.cancel_job(queued_id) == "cancelled"
    assert jobs_db.get_upload_job(queued_id)["status"] == "cancelled"
    assert service.queue_position(queued_id) is None

    hold.set()
    assert _wait_for(lambda: service.active_job_count() == 0)
    assert ran == []  # it never ran


def test_cancelling_a_running_job_reports_that_it_is_stopping(jobs_db, service):
    """A running job answers 'cancelling', and says so until it stops."""
    hold = threading.Event()
    job_id = jobs_db.create_upload_job("c1", 1, job_type="index")
    service._submit(_QueuedJob(job_id, "c1", lambda: hold.wait(timeout=5), ()))
    assert _wait_for(lambda: service.active_job_count() == 1)

    assert service.cancel_job(job_id) == "cancelling"
    assert service.cancel_requested(job_id) is True
    assert service._is_cancelled(job_id) is True

    hold.set()
    assert _wait_for(lambda: service.active_job_count() == 0)
    assert service.cancel_requested(job_id) is False


def test_cancelling_an_unknown_job_reports_nothing_happened(service):
    assert service.cancel_job(12345) == ""
    assert service.cancel_job(12345, force=True) == "cancelled"


# ── Job rows ────────────────────────────────────────────────────────────


def test_completion_clears_the_current_file(jobs_db):
    """A finished job stops advertising the file it was working on.

    update_upload_job dropped every None, so the completion write's
    current_file=None was silently ignored and finished jobs kept showing
    the last file they touched.
    """
    job_id = jobs_db.create_upload_job("c1", 2, job_type="index")
    jobs_db.update_upload_job(job_id, status="running", current_file="big.pdf")
    assert jobs_db.get_upload_job(job_id)["current_file"] == "big.pdf"

    jobs_db.update_upload_job(job_id, status="completed", current_file=None)
    assert jobs_db.get_upload_job(job_id)["current_file"] is None


def test_partial_updates_do_not_wipe_untouched_columns(jobs_db):
    """Fields a progress write says nothing about keep their values."""
    job_id = jobs_db.create_upload_job("c1", 2, job_type="index")
    jobs_db.update_upload_job(job_id, status="running", current_file="a.pdf",
                              processed_files=1, phase="embedding")

    jobs_db.update_upload_job(job_id, processed_files=2)

    row = jobs_db.get_upload_job(job_id)
    assert row["processed_files"] == 2
    assert row["current_file"] == "a.pdf"
    assert row["phase"] == "embedding"


def test_recent_jobs_returns_finished_jobs_newest_first(jobs_db):
    """Finished jobs stay reachable — that's where failed files are listed."""
    first = jobs_db.create_upload_job("c1", 1, job_type="index")
    jobs_db.update_upload_job(first, status="completed",
                              result_summary='{"failed_files": []}')
    second = jobs_db.create_upload_job("c2", 4, job_type="upload")
    jobs_db.update_upload_job(
        second, status="completed",
        result_summary='{"failed_files": [{"filename": "broken.pdf", "error": "bad"}]}',
    )

    rows = jobs_db.get_recent_upload_jobs(limit=10)
    assert [r["id"] for r in rows] == [second, first]
    assert jobs_db.get_all_active_upload_jobs() == []  # neither is active

    only_c2 = jobs_db.get_recent_upload_jobs(limit=10, collection_id="c2")
    assert [r["id"] for r in only_c2] == [second]

"""
Tests for the in-memory async-job registry in web.shared_state.

These encode WHY the registry exists: a single gunicorn worker must track
background analysis jobs so the frontend can poll status without holding a
long synchronous request open (which the platform proxy would close).
"""
import time

import pytest

from web import shared_state


@pytest.fixture(autouse=True)
def clear_jobs():
    """Each test starts with an empty registry."""
    shared_state.JOBS.clear()
    yield
    shared_state.JOBS.clear()


def test_create_job_starts_running_at_step_zero():
    job_id = shared_state.create_job()
    assert job_id
    job = shared_state.get_job(job_id)
    assert job["status"] == "running"
    assert job["step"] == 0
    assert job["report_id"] is None
    assert job["error"] is None


def test_get_unknown_job_returns_none():
    assert shared_state.get_job("does-not-exist") is None


def test_update_job_sets_progress_fields():
    job_id = shared_state.create_job()
    shared_state.update_job(job_id, step=3, label="计算指标与评分")
    job = shared_state.get_job(job_id)
    assert job["step"] == 3
    assert job["label"] == "计算指标与评分"


def test_create_job_rejects_when_at_concurrency_limit():
    """At MAX_CONCURRENT running jobs, new jobs are refused (returns None)
    so multiple heavy pipelines can't exhaust the single worker."""
    ids = [shared_state.create_job() for _ in range(shared_state.MAX_CONCURRENT)]
    assert all(ids)
    assert shared_state.create_job() is None


def test_finished_job_frees_a_concurrency_slot():
    """A done job must not count against the running limit."""
    ids = [shared_state.create_job() for _ in range(shared_state.MAX_CONCURRENT)]
    shared_state.update_job(ids[0], status="done")
    assert shared_state.create_job() is not None


def test_prune_removes_stale_jobs():
    job_id = shared_state.create_job()
    # Backdate so it looks abandoned.
    shared_state.JOBS[job_id]["created_at"] = time.time() - 7200
    shared_state.prune_jobs(max_age_seconds=3600)
    assert shared_state.get_job(job_id) is None

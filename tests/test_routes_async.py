"""
Tests for the async analysis endpoints.

WHY: a synchronous /analyze holds one HTTP request open for minutes, which the
platform proxy closes (ERR_CONNECTION_CLOSED). These endpoints let the request
return immediately with a job_id the frontend polls, so the connection is never
held open long enough to be cut.
"""
import json
import time

import pytest

from web import shared_state


@pytest.fixture(autouse=True)
def clear_jobs():
    shared_state.JOBS.clear()
    yield
    shared_state.JOBS.clear()


def _start(client, payload):
    return client.post(
        "/api/analyze/start",
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_start_returns_job_id_and_completes(client):
    """POST start returns a job_id immediately; the background job then runs to
    completion and exposes a report_id via the status endpoint."""
    resp = _start(client, {
        "company_name": "测试公司",
        "current_period": "2025年报",
        "financial_data": {"营业收入": 1000, "净利润": 100, "总资产": 5000},
    })
    assert resp.status_code == 200
    job_id = resp.get_json()["job_id"]
    assert job_id

    data = None
    for _ in range(100):
        data = client.get(f"/api/analyze/status/{job_id}").get_json()
        if data["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert data["status"] == "done", data.get("error")
    assert data["report_id"]


def test_status_unknown_job_returns_404(client):
    resp = client.get("/api/analyze/status/nope")
    assert resp.status_code == 404


def test_start_rejects_when_at_capacity(client):
    """When the worker is already at MAX_CONCURRENT running jobs, start returns
    429 instead of piling on another pipeline."""
    for _ in range(shared_state.MAX_CONCURRENT):
        shared_state.create_job()  # occupy all running slots
    resp = _start(client, {"company_name": "x", "financial_data": {"营业收入": 1}})
    assert resp.status_code == 429

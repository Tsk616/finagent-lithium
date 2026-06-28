"""
Shared in-memory state for FinAgent-Lithium.

Stores report history and states in memory. This data is lost on restart.
A future improvement would persist to disk or a database.

Also contains _save_report_state() which is used by multiple route modules
to record analysis results for history browsing and follow-up Q&A.
"""

import threading
import time
import uuid
from collections import deque

REPORT_HISTORY: deque = deque(maxlen=20)
REPORT_STATES: dict = {}


# ── Async analysis job registry ───────────────────────────────────────
# Background analysis runs in a thread; the frontend polls job status so it
# never holds a long synchronous request open (the platform proxy closes
# those before a multi-minute pipeline finishes). State is in-memory only:
# a single gunicorn worker shares this dict across its threads. Jobs are
# lost on restart by design (best-effort; the user retries).

JOBS: dict = {}
JOBS_LOCK = threading.Lock()
MAX_CONCURRENT = 2  # cap simultaneous pipelines to protect the 512MB worker


def _prune_locked(max_age_seconds: float) -> None:
    cutoff = time.time() - max_age_seconds
    stale = [jid for jid, j in JOBS.items() if j["created_at"] < cutoff]
    for jid in stale:
        JOBS.pop(jid, None)


def create_job():
    """Register a new running job. Returns a job_id, or None if the worker is
    already at MAX_CONCURRENT running jobs."""
    with JOBS_LOCK:
        _prune_locked(3600)
        running = sum(1 for j in JOBS.values() if j["status"] == "running")
        if running >= MAX_CONCURRENT:
            return None
        job_id = str(uuid.uuid4())[:12]
        JOBS[job_id] = {
            "status": "running",
            "step": 0,
            "label": "",
            "report_id": None,
            "error": None,
            "created_at": time.time(),
        }
        return job_id


def update_job(job_id: str, **fields) -> None:
    """Merge fields into an existing job record (no-op if unknown)."""
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(fields)


def get_job(job_id: str):
    """Return a copy of the job record, or None if unknown."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def prune_jobs(max_age_seconds: float = 3600) -> None:
    """Drop jobs older than max_age_seconds to bound memory growth."""
    with JOBS_LOCK:
        _prune_locked(max_age_seconds)


def save_report_state(state: dict) -> str:
    """Keep the latest reports available for history and follow-up Q&A.

    Returns a short report_id string. Automatically evicts stale entries
    that have rotated out of the REPORT_HISTORY deque.
    """
    report_id = str(uuid.uuid4())[:12]
    stored = dict(state)
    stored["report_id"] = report_id
    stored["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    REPORT_STATES[report_id] = stored

    item = {
        "report_id": report_id,
        "company_name": stored.get("company_name") or "未命名公司",
        "stock_code": stored.get("stock_code") or "",
        "current_period": stored.get("current_period") or "",
        "generated_at": stored["generated_at"],
        "data_completeness": stored.get("data_completeness", 0),
        "weighted_score": (stored.get("weighted_score") or {}).get("score"),
        "sector": stored.get("sector_level2") or stored.get("sector_level1") or "",
    }
    REPORT_HISTORY.appendleft(item)

    # Evict states whose report_id is no longer in the history deque
    active_ids = {row["report_id"] for row in REPORT_HISTORY}
    for old_id in list(REPORT_STATES.keys()):
        if old_id not in active_ids:
            REPORT_STATES.pop(old_id, None)
    return report_id

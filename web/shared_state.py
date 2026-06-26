"""
Shared in-memory state for FinAgent-Lithium.

Stores report history and states in memory. This data is lost on restart.
A future improvement would persist to disk or a database.

Also contains _save_report_state() which is used by multiple route modules
to record analysis results for history browsing and follow-up Q&A.
"""

import time
import uuid
from collections import deque

REPORT_HISTORY: deque = deque(maxlen=20)
REPORT_STATES: dict = {}


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

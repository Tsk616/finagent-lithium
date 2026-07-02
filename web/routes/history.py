"""
History routes for FinAgent-Lithium.

Provides endpoints for listing recent reports and viewing a specific
report from in-memory history. Reports are lost on server restart.

Does NOT re-run the analysis pipeline -- it reads from stored state.
"""

from flask import Blueprint, render_template, jsonify

from web.template_data import build_template_data
from web.shared_state import REPORT_HISTORY, REPORT_STATES

bp = Blueprint("history", __name__)


@bp.route("/api/history")
def api_history():
    """Return recent report history."""
    return jsonify({"items": list(REPORT_HISTORY)})


@bp.route("/local-report")
def local_report():
    """Read-only viewer for reports archived in the browser's localStorage.

    The page is a shell; JS reads the report id from the URL hash and renders
    the stored markdown client-side. No server state involved.
    """
    return render_template("local_report.html")


@bp.route("/report/<report_id>")
def view_report(report_id):
    """Render a report from the in-memory recent history."""
    state = REPORT_STATES.get(report_id)
    if not state:
        return render_template("index.html", error="报告记录不存在或服务已重启。", history_items=list(REPORT_HISTORY)), 404
    template_data = build_template_data(state)
    template_data["report_id"] = report_id
    return render_template("report.html", **template_data)

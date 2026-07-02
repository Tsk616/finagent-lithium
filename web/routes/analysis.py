"""
Analysis routes for FinAgent-Lithium.

Handles the main analysis flow: landing page, file/JSON upload,
pipeline execution, report rendering, demo mode, and report download.
Does NOT handle follow-up Q&A, peer comparison, or history browsing.
"""

import os
import tempfile
import threading
import traceback

from flask import Blueprint, render_template, request, jsonify, Response

from web.workflow import run_pipeline, KB
from web.template_data import build_template_data, parse_json_field
from web.shared_state import (
    REPORT_HISTORY,
    REPORT_STATES,
    save_report_state,
    create_job,
    get_job,
    update_job,
)
from nodes.data_extractor import extract as extract_from_file

bp = Blueprint("analysis", __name__)

ALLOWED_UPLOAD_EXTS = {".xlsx", ".xls", ".pdf"}

# Gate for the synchronous API endpoints: one pipeline at a time so an API
# caller can't stack expensive LLM/Wind runs alongside the async job slots.
_SYNC_PIPELINE_SLOT = threading.BoundedSemaphore(1)


def _parse_analyze_input(req) -> dict:
    """Extract analysis inputs from a request (file upload + form/JSON).

    Runs entirely in the request thread (Flask ``request`` is thread-local and
    must not be touched from a background worker). Returns a plain dict whose
    keys line up with ``run_pipeline`` parameters, plus ``_upload_msg``.
    """
    financial_data = {}
    upload_msg = ""

    uploaded_file = req.files.get("file")
    if uploaded_file and uploaded_file.filename:
        suffix = os.path.splitext(uploaded_file.filename)[1].lower()
        if suffix not in ALLOWED_UPLOAD_EXTS:
            raise ValueError(f"不支持的文件格式: {suffix}。请上传 .xlsx/.xls/.pdf 文件。")
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(tmp_fd)  # Close the fd so pandas can open it
        uploaded_file.save(tmp_path)
        try:
            financial_data = extract_from_file(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # Windows file locking -- ignore
        upload_msg = f"从 {uploaded_file.filename} 提取到 {len(financial_data)} 个科目"

    if req.is_json:
        payload = req.get_json() or {}
    else:
        payload = {
            "company_name": req.form.get("company_name", ""),
            "stock_code": req.form.get("stock_code", ""),
            "current_period": req.form.get("current_period", ""),
            "primary_business": req.form.get("primary_business", "").split("\n") if req.form.get("primary_business") else [],
            "manual_sector": req.form.get("manual_sector", ""),
            "financial_data": parse_json_field(req.form.get("financial_data_json", "{}")),
        }
        if financial_data:
            payload["financial_data"] = financial_data

    if not financial_data:
        financial_data = payload.get("financial_data", {})

    return {
        "company_name": payload.get("company_name", ""),
        "stock_code": payload.get("stock_code", ""),
        "current_period": payload.get("current_period", ""),
        "primary_business": payload.get("primary_business", []),
        "manual_sector": payload.get("manual_sector") or None,
        "financial_data": financial_data,
        "notes_data": payload.get("notes_data", {}),
        "_upload_msg": upload_msg,
    }


def _run_job(job_id: str, parsed: dict) -> None:
    """Background worker: run the pipeline and record progress/result in the
    job registry. Never renders templates (no Flask app/request context here)."""
    try:
        def cb(step, label):
            update_job(job_id, step=step, label=label)

        state = run_pipeline(
            company_name=parsed["company_name"],
            stock_code=parsed["stock_code"],
            current_period=parsed["current_period"],
            primary_business=parsed["primary_business"],
            manual_sector=parsed["manual_sector"],
            financial_data=parsed["financial_data"],
            notes_data=parsed["notes_data"],
            progress_cb=cb,
        )
        report_id = save_report_state(state)
        update_job(job_id, status="done", step=5, report_id=report_id)
    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="error", error=str(e))


@bp.route("/")
def index():
    """Landing page: data input form."""
    return render_template("index.html", history_items=list(REPORT_HISTORY), initial_panel="upload")


@bp.route("/history")
def history_page():
    """Open the history module."""
    return render_template("index.html", history_items=list(REPORT_HISTORY), initial_panel="history")


@bp.route("/ask")
def ask_page():
    """Open the follow-up Q&A module."""
    return render_template("index.html", history_items=list(REPORT_HISTORY), initial_panel="ask")


@bp.route("/analyze", methods=["POST"])
def analyze():
    """Run analysis pipeline and render report.

    Supports:
      - File upload (.xlsx/.pdf) -> auto-extract financial data
      - JSON form data -> direct input
    """
    try:
        parsed = _parse_analyze_input(request)

        if not _SYNC_PIPELINE_SLOT.acquire(blocking=False):
            return render_template(
                "index.html",
                error="服务繁忙（已有分析在进行中），请稍候再试。",
                history_items=list(REPORT_HISTORY),
            ), 429
        try:
            state = run_pipeline(
                company_name=parsed["company_name"],
                stock_code=parsed["stock_code"],
                current_period=parsed["current_period"],
                primary_business=parsed["primary_business"],
                manual_sector=parsed["manual_sector"],
                financial_data=parsed["financial_data"],
                notes_data=parsed["notes_data"],
            )
        finally:
            _SYNC_PIPELINE_SLOT.release()

        template_data = build_template_data(state)
        if parsed["_upload_msg"]:
            template_data["upload_msg"] = parsed["_upload_msg"]
        template_data["report_id"] = save_report_state(state)

        return render_template("report.html", **template_data)

    except Exception as e:
        traceback.print_exc()
        return render_template("index.html", error=str(e), history_items=list(REPORT_HISTORY))


@bp.route("/api/analyze/start", methods=["POST"])
def analyze_start():
    """Kick off analysis in a background thread; return a job_id immediately.

    The frontend polls /api/analyze/status/<job_id> and redirects to
    /report/<report_id> on completion. This keeps the HTTP request short so the
    platform proxy never closes a long-held connection.
    """
    try:
        parsed = _parse_analyze_input(request)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    job_id = create_job()
    if job_id is None:
        return jsonify({"status": "busy", "message": "服务繁忙，请稍候重试"}), 429

    threading.Thread(target=_run_job, args=(job_id, parsed), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.route("/api/analyze/status/<job_id>")
def analyze_status(job_id):
    """Report background job progress. 404 if the job is unknown (e.g. lost on
    an instance restart) so the frontend can prompt a retry."""
    job = get_job(job_id)
    if job is None:
        return jsonify({"status": "not_found"}), 404
    return jsonify({
        "status": job["status"],
        "step": job["step"],
        "label": job["label"],
        "report_id": job["report_id"],
        "error": job["error"],
    })


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    """API endpoint: returns raw AnalysisState JSON.

    Synchronous by design (API consumers), so it is gated to one concurrent
    run. Web UI should use /api/analyze/start instead.
    """
    if not _SYNC_PIPELINE_SLOT.acquire(blocking=False):
        return jsonify({"status": "busy", "message": "服务繁忙，请稍候重试"}), 429
    try:
        payload = request.get_json() or {}
        state = run_pipeline(
            company_name=payload.get("company_name", ""),
            stock_code=payload.get("stock_code", ""),
            current_period=payload.get("current_period", ""),
            primary_business=payload.get("primary_business", []),
            manual_sector=payload.get("manual_sector"),
            financial_data=payload.get("financial_data", {}),
            notes_data=payload.get("notes_data", {}),
        )
        state["report_id"] = save_report_state(state)
        return jsonify(state)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        _SYNC_PIPELINE_SLOT.release()


@bp.route("/api/report/<report_id>.md")
def download_report(report_id):
    """Download the Markdown report of an already-generated analysis.

    Reads from the stored report state -- never re-runs the pipeline.
    """
    state = REPORT_STATES.get(report_id)
    if not state:
        return jsonify({"status": "error", "message": "报告不存在或服务已重启，请重新生成分析。"}), 404
    report = state.get("report_markdown", "")
    # HTTP headers are latin-1; Chinese company names need RFC 5987 filename*
    from urllib.parse import quote
    fname = quote(f"{state.get('company_name') or 'report'}_analysis.md")
    return Response(
        report,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"}
    )


def _run_demo_job(job_id: str, current_period: str) -> None:
    """Background worker for demo mode: Wind fetch + pipeline, like _run_job.

    The Wind fetch lives inside the job because it alone can take >10s --
    everything slow must stay off the request thread on the free tier.
    """
    try:
        def cb(step, label):
            update_job(job_id, step=step, label=label)

        financial_data = None
        try:
            from nodes.wind_adapter import fetch_financials
            cb(1, "获取 Wind 演示数据")
            financial_data = fetch_financials("300750.SZ", current_period)
        except Exception:
            financial_data = None

        wind_used = bool(financial_data)
        if not financial_data:
            from nodes.calculate_sector import _test_data_sample as sector_data
            financial_data = sector_data("宁德时代")

        state = run_pipeline(
            company_name="宁德时代",
            stock_code="300750",
            current_period=current_period,
            primary_business=["动力电池系统", "储能系统", "电池材料"],
            financial_data=financial_data,
            progress_cb=cb,
        )
        if wind_used:
            state["_wind_enriched"] = True
            state["_wind_accounts_count"] = len(financial_data)
        report_id = save_report_state(state)
        update_job(job_id, status="done", step=5, report_id=report_id)
    except Exception as e:
        traceback.print_exc()
        update_job(job_id, status="error", error=str(e))


@bp.route("/api/demo/start", methods=["POST"])
def demo_start():
    """Kick off the demo analysis as a background job (same contract as
    /api/analyze/start)."""
    current_period = (request.get_json(silent=True) or {}).get("period") or "2025年报"
    job_id = create_job()
    if job_id is None:
        return jsonify({"status": "busy", "message": "服务繁忙，请稍候重试"}), 429
    threading.Thread(target=_run_demo_job, args=(job_id, current_period), daemon=True).start()
    return jsonify({"job_id": job_id})


@bp.route("/demo")
def demo():
    """Demo entry: renders the workbench which auto-starts an async demo job.

    The old synchronous version ran the full pipeline in the request thread
    and was killed by the platform proxy after ~60s. Query param `period`
    (e.g. '2024年报', '最新一期') is forwarded to the job.
    """
    current_period = request.args.get("period", "2025年报")
    return render_template(
        "index.html",
        history_items=list(REPORT_HISTORY),
        initial_panel="upload",
        auto_demo_period=current_period,
    )

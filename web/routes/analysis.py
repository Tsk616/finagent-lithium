"""
Analysis routes for FinAgent-Lithium.

Handles the main analysis flow: landing page, file/JSON upload,
pipeline execution, report rendering, demo mode, and report download.
Does NOT handle follow-up Q&A, peer comparison, or history browsing.
"""

import os
import tempfile

from flask import Blueprint, render_template, request, jsonify, Response

from web.workflow import run_pipeline, KB
from web.template_data import build_template_data, parse_json_field
from web.shared_state import REPORT_HISTORY, save_report_state
from nodes.data_extractor import extract as extract_from_file

bp = Blueprint("analysis", __name__)


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
        financial_data = {}
        notes_data = {}
        upload_msg = ""

        # ---- Check for file upload ----
        uploaded_file = request.files.get("file")
        if uploaded_file and uploaded_file.filename:
            suffix = os.path.splitext(uploaded_file.filename)[1]
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

        # ---- Parse form/JSON input ----
        if request.is_json:
            payload = request.get_json()
        else:
            payload = {
                "company_name": request.form.get("company_name", ""),
                "stock_code": request.form.get("stock_code", ""),
                "current_period": request.form.get("current_period", ""),
                "primary_business": request.form.get("primary_business", "").split("\n") if request.form.get("primary_business") else [],
                "manual_sector": request.form.get("manual_sector", ""),
                "financial_data": parse_json_field(request.form.get("financial_data_json", "{}")),
            }
            # Merge file-extracted data if available (form + file hybrid)
            if financial_data:
                payload["financial_data"] = financial_data
            if upload_msg:
                payload["_upload_msg"] = upload_msg

        # Use JSON-form data if no file was uploaded
        if not financial_data:
            financial_data = payload.get("financial_data", {})
        notes_data = payload.get("notes_data", {})

        company_name = payload.get("company_name", "")
        stock_code = payload.get("stock_code", "")
        current_period = payload.get("current_period", "")
        primary_business = payload.get("primary_business", [])
        manual_sector = payload.get("manual_sector") or None

        # Run the full pipeline
        state = run_pipeline(
            company_name=company_name,
            stock_code=stock_code,
            current_period=current_period,
            primary_business=primary_business,
            manual_sector=manual_sector,
            financial_data=financial_data,
            notes_data=notes_data,
        )

        # Build template-safe data
        template_data = build_template_data(state)
        if upload_msg:
            template_data["upload_msg"] = upload_msg
        template_data["report_id"] = save_report_state(state)

        return render_template("report.html", **template_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template("index.html", error=str(e), history_items=list(REPORT_HISTORY))


@bp.route("/api/analyze", methods=["POST"])
def api_analyze():
    """API endpoint: returns raw AnalysisState JSON."""
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


@bp.route("/api/report.md", methods=["POST"])
def download_report():
    """Download the generated Markdown report."""
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
        report = state.get("report_markdown", "")
        return Response(
            report,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={state.get('company_name','report')}_analysis.md"}
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/demo")
def demo():
    """Demo page with CATL data. Uses Wind live data when available.

    Query params:
        period: Report period, e.g. '2025年报', '2024年报', '最新一期'
                Default: '2025年报'
    """
    current_period = request.args.get("period", "2025年报")
    financial_data = None

    # Try Wind live data first
    try:
        from nodes.wind_adapter import fetch_financials
        financial_data = fetch_financials("300750.SZ", current_period)
    except Exception:
        pass

    wind_used = False
    wind_count = 0
    if not financial_data:
        # Fallback to hardcoded sample data
        from nodes.calculate_sector import _test_data_sample as sector_data
        financial_data = sector_data("宁德时代")
    else:
        wind_used = True
        wind_count = len(financial_data)

    state = run_pipeline(
        company_name="宁德时代",
        stock_code="300750",
        current_period=current_period,
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        financial_data=financial_data,
    )
    if wind_used:
        state["_wind_enriched"] = True
        state["_wind_accounts_count"] = wind_count
    template_data = build_template_data(state)
    template_data["report_id"] = save_report_state(state)
    return render_template("report.html", **template_data)

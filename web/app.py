"""
FinAgent-Lithium Web Application.

Flask-based web UI for the lithium battery financial analysis system.
Serves the report HTML page and provides API endpoints.
"""

import json
import sys
from pathlib import Path

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, render_template, request, jsonify, Response
from web.workflow import run_pipeline, KB
from nodes.data_extractor import extract as extract_from_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB max upload


# ── Routes ────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    """Health check endpoint for cloud platform monitoring."""
    kb_ok = KB is not None
    return jsonify({
        "status": "ok" if kb_ok else "degraded",
        "kb_loaded": kb_ok,
    })


@app.route("/")
def index():
    """Landing page: data input form."""
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    """Run analysis pipeline and render report.

    Supports:
      - File upload (.xlsx/.pdf) → auto-extract financial data
      - JSON form data → direct input
    """
    try:
        financial_data = {}
        notes_data = {}
        upload_msg = ""

        # ── Check for file upload ──
        uploaded_file = request.files.get("file")
        if uploaded_file and uploaded_file.filename:
            import tempfile, os
            suffix = os.path.splitext(uploaded_file.filename)[1]
            import tempfile as tmp_module
            tmp_fd, tmp_path = tmp_module.mkstemp(suffix=suffix)
            os.close(tmp_fd)  # Close the fd so pandas can open it
            uploaded_file.save(tmp_path)
            try:
                financial_data = extract_from_file(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass  # Windows file locking — ignore
            upload_msg = f"从 {uploaded_file.filename} 提取到 {len(financial_data)} 个科目"

        # ── Parse form/JSON input ──
        if request.is_json:
            payload = request.get_json()
        else:
            payload = {
                "company_name": request.form.get("company_name", ""),
                "stock_code": request.form.get("stock_code", ""),
                "current_period": request.form.get("current_period", ""),
                "primary_business": request.form.get("primary_business", "").split("\n") if request.form.get("primary_business") else [],
                "manual_sector": request.form.get("manual_sector", ""),
                "financial_data": _parse_json_field(request.form.get("financial_data_json", "{}")),
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
        template_data = _build_template_data(state)
        if upload_msg:
            template_data["upload_msg"] = upload_msg

        return render_template("report.html", **template_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template("index.html", error=str(e))


@app.route("/api/analyze", methods=["POST"])
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
        return jsonify(state)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/report.md", methods=["POST"])
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


@app.route("/demo")
def demo():
    """Demo page with pre-loaded 宁德时代 data."""
    from nodes.calculate_sector import _test_data_sample as sector_data
    data = sector_data("宁德时代")

    state = run_pipeline(
        company_name="宁德时代",
        stock_code="300750",
        current_period="2025年报",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        financial_data=data,
    )
    template_data = _build_template_data(state)
    return render_template("report.html", **template_data)


# ── Template data builder ─────────────────────────────────────────────

def _build_template_data(state: dict) -> dict:
    """Build a template-safe data dict from pipeline state."""

    def risk_color(risk_str: str) -> str:
        if "正常" in risk_str: return "normal"
        if "警惕" in risk_str: return "warning"
        if "高风险" in risk_str: return "danger"
        return "pending"

    def simplify_inds(indicators: dict) -> list:
        result = []
        for name, ind in indicators.items():
            val = ind.get("value")
            risk = str(ind.get("risk_level", ""))
            result.append({
                "name": name,
                "value": f"{val:.2f}{ind.get('unit','')}" if val is not None else None,
                "risk_level": risk,
                "risk_color": risk_color(risk),
                "normal_range": ind.get("normal_range", ""),
                "warning_range": ind.get("warning_range", ""),
                "high_risk_range": ind.get("high_risk_range", ""),
                "explanation": ind.get("single_mapping", ind.get("explanation", ""))[:150],
                "formula": ind.get("formula", ""),
                "is_pending": "待补充" in risk,
                "is_key": ind.get("is_key_indicator", False),
                "is_fatal": ind.get("is_fatal_indicator", False),
            })
        return result

    general_indicators = state.get("general_indicators", {})
    general_list = simplify_inds(general_indicators)
    computable = [i for i in general_list if not i["is_pending"]]
    pending = [i for i in general_list if i["is_pending"]]

    sector_indicators = state.get("sector_indicators", {})
    sector_tabs = []
    for sec_label, categories in sector_indicators.items():
        tabs = []
        for cat_name, indicators in categories.items():
            tabs.append({
                "name": cat_name,
                "indicators": simplify_inds(indicators),
            })
        sector_tabs.append({"label": sec_label, "tabs": tabs})

    linkage = state.get("linkage_diagnosis", [])
    linkage_sorted = sorted(linkage, key=lambda d: (
        0 if "高危" in d.get("status", "") else
        1 if "风险" in d.get("status", "") else
        2 if "衰退" in d.get("status", "") else 3
    ))

    anomalies = state.get("anomaly_signals", [])

    return {
        "company_name": state.get("company_name", ""),
        "stock_code": state.get("stock_code", ""),
        "current_period": state.get("current_period", ""),
        "sector_level1": state.get("sector_level1", ""),
        "sector_level2": state.get("sector_level2", ""),
        "sector_characteristics": state.get("sector_characteristics", ""),
        "analysis_focus": state.get("analysis_focus", ""),
        "sub_sectors": state.get("sub_sectors"),
        "is_integrated": bool(state.get("sub_sectors")),
        "general_indicators": general_list,
        "general_computable": computable,
        "general_pending": pending,
        "data_completeness": state.get("data_completeness", 0),
        "sector_tabs": sector_tabs,
        "key_indicators": simplify_inds(state.get("key_indicators_for_linkage", {})),
        "linkage_diagnosis": linkage_sorted,
        "anomaly_signals": anomalies,
        "has_anomalies": len(anomalies) > 0,
        "skipped_external": state.get("skipped_external_rules", 0),
        "error_log": state.get("error_log", []),
        "report_markdown": state.get("report_markdown", ""),
        "status": state.get("status", "unknown"),
    }


def _parse_json_field(raw: str) -> dict:
    """Parse a JSON field from form data."""
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ── Main ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os as _os
    port = int(_os.environ.get("PORT", 5002))
    debug = _os.environ.get("FLASK_DEBUG", "1") == "1"
    print(f"FinAgent-Lithium Web UI")
    print(f"KB loaded: {KB is not None}")
    print(f"Starting at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)

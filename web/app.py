"""
FinAgent-Lithium Web Application.

Flask-based web UI for the lithium battery financial analysis system.
Serves the report HTML page and provides API endpoints.
"""

import json
import os
import sys
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from flask import Flask, render_template, request, jsonify, Response
from web.workflow import run_pipeline, KB
from nodes.calculate_general import calculate_general, annotate_formulas
from nodes.data_extractor import extract as extract_from_file
from nodes.llm_client import call_llm

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB max upload

_REPORT_HISTORY = deque(maxlen=20)
_REPORT_STATES = {}


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
    return render_template("index.html", history_items=list(_REPORT_HISTORY), initial_panel="upload")


@app.route("/history")
def history_page():
    """Open the history module."""
    return render_template("index.html", history_items=list(_REPORT_HISTORY), initial_panel="history")


@app.route("/ask")
def ask_page():
    """Open the follow-up Q&A module."""
    return render_template("index.html", history_items=list(_REPORT_HISTORY), initial_panel="ask")


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
        template_data["report_id"] = _save_report_state(state)

        return render_template("report.html", **template_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template("index.html", error=str(e), history_items=list(_REPORT_HISTORY))


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
        state["report_id"] = _save_report_state(state)
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
    """Demo page with 宁德时代 data. Uses Wind live data when available.

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
    template_data = _build_template_data(state)
    template_data["report_id"] = _save_report_state(state)
    return render_template("report.html", **template_data)


@app.route("/report/<report_id>")
def view_report(report_id):
    """Render a report from the in-memory recent history."""
    state = _REPORT_STATES.get(report_id)
    if not state:
        return render_template("index.html", error="报告记录不存在或服务已重启。", history_items=list(_REPORT_HISTORY)), 404
    template_data = _build_template_data(state)
    template_data["report_id"] = report_id
    return render_template("report.html", **template_data)


@app.route("/api/history")
def api_history():
    """Return recent report history."""
    return jsonify({"items": list(_REPORT_HISTORY)})


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """Answer a follow-up question based on a generated report."""
    payload = request.get_json() or {}
    report_id = payload.get("report_id", "")
    question = str(payload.get("question", "")).strip()
    if not question:
        return jsonify({"status": "error", "message": "question is required"}), 400

    state = _REPORT_STATES.get(report_id)
    if not state:
        return jsonify({"status": "error", "message": "report_id not found or expired"}), 404

    answer = _answer_followup_question(state, question)
    return jsonify({"status": "ok", "answer": answer})


@app.route("/compare", methods=["POST"])
def compare_peers():
    """Compare multiple uploaded peer reports with a compact table and radar data."""
    uploaded_files = [f for f in request.files.getlist("peer_files") if f and f.filename]
    if len(uploaded_files) < 2:
        return render_template(
            "index.html",
            error="同行对比至少需要上传 2 个同年份年报或财报文件。",
            history_items=list(_REPORT_HISTORY),
        ), 400

    peer_rows = []
    for file_obj in uploaded_files[:8]:
        try:
            financial_data = _extract_uploaded_file(file_obj)
            peer_rows.append(_build_peer_compare_row(file_obj.filename, financial_data))
        except Exception as exc:
            peer_rows.append({
                "company": Path(file_obj.filename).stem,
                "error": str(exc),
                "metrics": {},
                "raw": {},
            })

    comparison = _build_peer_comparison(peer_rows)
    return render_template("compare.html", comparison=comparison, peers=peer_rows)


def _save_report_state(state: dict) -> str:
    """Keep the latest reports available for history and follow-up Q&A."""
    report_id = str(uuid.uuid4())[:12]
    stored = dict(state)
    stored["report_id"] = report_id
    stored["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _REPORT_STATES[report_id] = stored

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
    _REPORT_HISTORY.appendleft(item)

    active_ids = {row["report_id"] for row in _REPORT_HISTORY}
    for old_id in list(_REPORT_STATES.keys()):
        if old_id not in active_ids:
            _REPORT_STATES.pop(old_id, None)
    return report_id


def _answer_followup_question(state: dict, question: str) -> str:
    """Use report evidence for follow-up Q&A, with a conservative fallback."""
    compact_state = {
        "company_name": state.get("company_name"),
        "stock_code": state.get("stock_code"),
        "period": state.get("current_period"),
        "sector": state.get("sector_level2"),
        "data_completeness": state.get("data_completeness"),
        "weighted_score": state.get("weighted_score"),
        "risk_summary": state.get("risk_summary"),
        "metric_interpretations": (state.get("metric_interpretations") or [])[:12],
        "industry_benchmark_insights": state.get("industry_benchmark_insights"),
        "macro_insights": state.get("macro_insights"),
        "anomaly_signals": state.get("anomaly_signals") or [],
    }
    system_prompt = (
        "You are FinAgent, a financial analysis assistant. Answer in Chinese. "
        "Only use the supplied report data. If the data is insufficient, say so clearly. "
        "Do not invent numbers, peers, macro data, or investment advice."
    )
    user_message = json.dumps(
        {"question": question, "report_data": compact_state},
        ensure_ascii=False,
        default=str,
    )
    answer = call_llm(system_prompt, user_message)
    if answer:
        return answer.strip()
    return _fallback_followup_answer(compact_state, question)


def _fallback_followup_answer(compact_state: dict, question: str) -> str:
    """Deterministic fallback when the LLM is unavailable."""
    score = (compact_state.get("weighted_score") or {}).get("score")
    risks = compact_state.get("risk_summary") or {}
    anomalies = compact_state.get("anomaly_signals") or []
    lines = [
        f"基于当前报告回答：{compact_state.get('company_name') or '该公司'}"
        f"（{compact_state.get('period') or '报告期未注明'}）。"
    ]
    if score is not None:
        lines.append(f"综合评分为 {score}/100。")
    if risks.get("summary"):
        lines.append(str(risks["summary"]))
    if anomalies:
        lines.append(f"报告中检测到 {len(anomalies)} 条异常信号，应优先查看异常警报章节。")

    matched = []
    q = question.lower()
    for item in compact_state.get("metric_interpretations") or []:
        metric = str(item.get("metric", ""))
        if metric and (metric.lower() in q or any(token in q for token in metric.lower().split())):
            matched.append(item)
    for item in matched[:3]:
        lines.append(f"{item.get('metric')}：{item.get('interpretation')}")
    if not matched:
        lines.append("当前离线回答只能基于已生成的评分、异常和指标解读摘要；更细的归因需要 LLM 服务可用。")
    return "\n".join(lines)


def _extract_uploaded_file(file_obj) -> dict:
    suffix = os.path.splitext(file_obj.filename)[1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        file_obj.save(tmp_path)
        return extract_from_file(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _build_peer_compare_row(filename: str, financial_data: dict) -> dict:
    general = annotate_formulas(calculate_general(financial_data=financial_data)["general_indicators"])
    selected_metrics = [
        "销售毛利率",
        "扣非销售净利率",
        "净利润现金含量",
        "资产负债率",
        "流动比率",
        "速动比率",
    ]
    metrics = {}
    for name in selected_metrics:
        item = general.get(name)
        if item:
            metrics[name] = {
                "value": item.get("value"),
                "unit": item.get("unit", ""),
                "risk_level": str(item.get("risk_level", "")),
            }
    return {
        "company": Path(filename).stem,
        "metrics": metrics,
        "raw": {
            "营业收入": financial_data.get("营业收入"),
            "净利润": financial_data.get("净利润"),
            "总资产": financial_data.get("总资产"),
            "所有者权益": financial_data.get("所有者权益") or financial_data.get("股东权益合计"),
        },
        "error": "",
    }


def _build_peer_comparison(peer_rows: list) -> dict:
    metric_names = []
    for peer in peer_rows:
        for name in peer.get("metrics", {}):
            if name not in metric_names:
                metric_names.append(name)

    lower_better = {"资产负债率"}
    table = []
    for name in metric_names:
        values = [
            (peer["company"], peer["metrics"][name]["value"])
            for peer in peer_rows
            if peer.get("metrics", {}).get(name, {}).get("value") is not None
        ]
        best_company = worst_company = ""
        if values:
            ordered = sorted(values, key=lambda x: x[1], reverse=name not in lower_better)
            best_company = ordered[0][0]
            worst_company = ordered[-1][0]
        table.append({
            "metric": name,
            "unit": next((peer["metrics"][name].get("unit", "") for peer in peer_rows if name in peer.get("metrics", {})), ""),
            "best_company": best_company,
            "worst_company": worst_company,
            "values": [
                {
                    "company": peer["company"],
                    "value": peer.get("metrics", {}).get(name, {}).get("value"),
                    "risk_level": peer.get("metrics", {}).get(name, {}).get("risk_level", ""),
                    "is_best": peer["company"] == best_company,
                    "is_worst": peer["company"] == worst_company,
                }
                for peer in peer_rows
            ],
        })

    radar = _build_peer_radar(peer_rows, metric_names[:6], lower_better)
    return {
        "metric_table": table,
        "radar": radar,
        "peer_count": len(peer_rows),
        "available_metric_count": len(metric_names),
    }


def _build_peer_radar(peer_rows: list, metric_names: list, lower_better: set) -> list:
    radar = []
    for peer in peer_rows:
        points = []
        for name in metric_names:
            values = [
                p.get("metrics", {}).get(name, {}).get("value")
                for p in peer_rows
                if p.get("metrics", {}).get(name, {}).get("value") is not None
            ]
            value = peer.get("metrics", {}).get(name, {}).get("value")
            if value is None or not values:
                score = 0
            else:
                lo, hi = min(values), max(values)
                if hi == lo:
                    score = 70
                else:
                    score = (value - lo) / (hi - lo) * 100
                    if name in lower_better:
                        score = 100 - score
            points.append({"metric": name, "score": round(score, 1)})
        radar.append({"company": peer["company"], "points": points})
    return radar


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
                "value": _format_ind_value(val, ind.get('unit', '')),
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
                "display_name": ind.get("display_name", name),
                "canonical_name": ind.get("canonical_name", name),
                "metric_category": ind.get("metric_category", ""),
                "metric_type": ind.get("metric_type", ""),
                "weight": ind.get("weight"),
                "weight_logic": ind.get("weight_logic", ""),
            })
        return result

    def group_metric_settings(items: list) -> list:
        groups = {}
        for item in items:
            key = item.get("metric_type") or item.get("metric_category") or "未分类"
            groups.setdefault(key, []).append(item)
        return [
            {"name": name, "indicators": indicators}
            for name, indicators in groups.items()
        ]

    def build_metric_blocks(items: list) -> list:
        blocks = []
        for group in group_metric_settings(items):
            indicators = group["indicators"]
            if not indicators:
                continue
            abnormal = [
                i for i in indicators
                if i.get("risk_color") in ("warning", "danger")
            ]
            available = [i for i in indicators if not i.get("is_pending")]
            sorted_core = sorted(
                indicators,
                key=lambda i: (
                    0 if i.get("is_key") or i.get("is_fatal") else 1,
                    -(i.get("weight") or 0),
                    0 if not i.get("is_pending") else 1,
                ),
            )
            blocks.append({
                "name": group["name"],
                "summary": {
                    "total": len(indicators),
                    "available": len(available),
                    "abnormal": len(abnormal),
                    "pending": len(indicators) - len(available),
                },
                "core_indicators": sorted_core[:4],
                "details": indicators,
            })
        return blocks

    general_indicators = state.get("general_indicators", {})
    general_list = simplify_inds(general_indicators)
    computable = [i for i in general_list if not i["is_pending"]]
    pending = [i for i in general_list if i["is_pending"]]

    sector_indicators = state.get("sector_indicators", {})
    sector_tabs = []
    metric_settings_items = list(general_list)
    for sec_label, categories in sector_indicators.items():
        tabs = []
        for cat_name, indicators in categories.items():
            simplified_sector_items = simplify_inds(indicators)
            metric_settings_items.extend(simplified_sector_items)
            tabs.append({
                "name": cat_name,
                "indicators": simplified_sector_items,
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
        "weighted_score": state.get("weighted_score", {}),
        "metric_config_summary": state.get("metric_config_summary", {}),
        "metric_settings_groups": group_metric_settings(metric_settings_items),
        "metric_blocks": build_metric_blocks(metric_settings_items),
        "metric_interpretations": state.get("metric_interpretations", []),
        "interp_lookup": {
            item["metric"]: item.get("interpretation", "")
            for item in state.get("metric_interpretations", [])
            if item.get("metric")
        },
        "macro_context": state.get("macro_context", {}),
        "macro_insights": state.get("macro_insights", {}),
        "industry_benchmark_insights": state.get("industry_benchmark_insights", {}),
        "analysis_models": state.get("analysis_models", {}),
        "risk_summary": state.get("risk_summary", {}),
        "wind_status": state.get("wind_status", {}),
        "wind_fetch_attempted": state.get("_wind_fetch_attempted", False),
        "wind_fetch_error": state.get("_wind_fetch_error", ""),
        "industry_comparison": state.get("industry_comparison", {}),
        "status": state.get("status", "unknown"),
        "wind_enriched": state.get("_wind_enriched", False),
        "wind_accounts_count": state.get("_wind_accounts_count", 0),
        "wind_classified": state.get("_wind_classified", False),
        # Peer comparison
        "peer_comparison": _format_peer_data(state.get("_peer_comparison", [])),
        "has_peers": bool(state.get("_peer_comparison")),
        # Macro context
        "lithium_trend": state.get("_lithium_trend"),
    }


def _format_ind_value(val, unit: str) -> str:
    """Format indicator value with unit, suppressing meaningless units."""
    if val is None:
        return None
    # Suppress "无" unit (means dimensionless but looks confusing)
    if not unit or unit == "无":
        return f"{val:.2f}"
    return f"{val:.2f}{unit}"


# Wind code → company short name lookup (common lithium battery peers)
_WINDCODE_NAME_MAP = {
    "300750.SZ": "宁德时代",
    "002594.SZ": "比亚迪",
    "002460.SZ": "赣锋锂业",
    "002466.SZ": "天齐锂业",
    "300014.SZ": "亿纬锂能",
    "688005.SH": "容百科技",
    "300450.SZ": "先导智能",
    "603799.SH": "华友钴业",
    "000792.SZ": "盐湖股份",
    "002008.SZ": "大族激光",
    "600549.SH": "厦门钨业",
    "600482.SH": "中国动力",
    "601168.SH": "西部矿业",
    "300390.SZ": "天华新能",
    "002709.SZ": "天赐材料",
    "002080.SZ": "中材科技",
}


def _format_peer_data(peers: list) -> list:
    """Format peer comparison data for template rendering."""
    if not peers:
        return []
    result = []
    for p in peers:
        wc = p.get("windcode", "")
        item = {
            "windcode": wc,
            "name": p.get("name") or _WINDCODE_NAME_MAP.get(wc, wc),
            "is_current": p.get("is_current", False),
        }
        # Format large numbers for display
        for key, label in [("revenue", "营业收入"), ("profit", "净利润"),
                           ("assets", "总资产"), ("equity", "净资产")]:
            val = p.get(key)
            if val is not None:
                if abs(val) >= 1e12:
                    item[key] = f"{val/1e12:.2f}万亿"
                elif abs(val) >= 1e8:
                    item[key] = f"{val/1e8:.0f}亿"
                else:
                    item[key] = f"{val:.0f}"
            else:
                item[key] = "-"
        result.append(item)
    return result


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

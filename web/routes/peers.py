"""
Peer comparison routes for FinAgent-Lithium.

Handles two comparison modes:
  1. File-based: users upload multiple financial reports for side-by-side comparison.
  2. Wind-based: users provide stock codes and data is fetched live from Wind.

Does NOT run the full analysis pipeline -- it computes a focused metric subset.
"""

import os
import tempfile
from pathlib import Path

from flask import Blueprint, render_template, request, jsonify

from nodes.calculate_general import calculate_general, annotate_formulas
from nodes.data_extractor import extract as extract_from_file
from web.template_data import WINDCODE_NAME_MAP
from web.shared_state import REPORT_HISTORY

bp = Blueprint("peers", __name__)


@bp.route("/compare", methods=["POST"])
def compare_peers():
    """Compare multiple uploaded peer reports with a compact table and radar data."""
    uploaded_files = [f for f in request.files.getlist("peer_files") if f and f.filename]
    if len(uploaded_files) < 2:
        return render_template(
            "index.html",
            error="同行对比至少需要上传 2 个同年份年报或财报文件。",
            history_items=list(REPORT_HISTORY),
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


@bp.route("/api/peer-compare", methods=["POST"])
def api_peer_compare():
    """Fetch peer comparison data from Wind by stock codes."""
    payload = request.get_json() or {}
    codes_str = payload.get("stock_codes", "")
    if not codes_str:
        return jsonify({"status": "error", "message": "stock_codes is required"}), 400

    codes = [c.strip() for c in codes_str.replace("，", ",").split(",") if c.strip()]
    if len(codes) < 1:
        return jsonify({"status": "error", "message": "至少输入1个股票代码"}), 400

    try:
        from nodes.wind_adapter import stock_code_to_windcode, fetch_financials
        peers = []
        for code in codes[:8]:
            windcode = stock_code_to_windcode(code)
            fin = fetch_financials(windcode, period="最新一期", timeout=12)
            if fin:
                peer = {"windcode": windcode, "name": WINDCODE_NAME_MAP.get(windcode, windcode)}
                rev = fin.get("营业收入")
                cost = fin.get("营业成本")
                profit = fin.get("净利润")
                assets = fin.get("总资产")
                equity = fin.get("净资产")
                for key, val in [("revenue", rev), ("profit", profit), ("assets", assets), ("equity", equity)]:
                    if val is not None:
                        peer[key] = f"{val/1e8:.0f}亿" if abs(val) >= 1e8 else f"{val:.0f}"
                    else:
                        peer[key] = "-"
                if rev and cost and rev != 0:
                    peer["gross_margin"] = f"{(rev - cost) / rev * 100:.1f}%"
                else:
                    peer["gross_margin"] = "-"
                if profit and equity and equity != 0:
                    peer["roe"] = f"{profit / equity * 100:.1f}%"
                else:
                    peer["roe"] = "-"
                peers.append(peer)
            else:
                peers.append({"windcode": windcode, "name": WINDCODE_NAME_MAP.get(windcode, windcode),
                              "revenue": "-", "profit": "-", "assets": "-", "equity": "-",
                              "gross_margin": "-", "roe": "-"})

        return jsonify({"status": "ok", "peers": peers})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ---- Helpers ----

def _extract_uploaded_file(file_obj) -> dict:
    """Save uploaded file to a temp path, extract financials, clean up."""
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
    """Build one row of the peer comparison table from raw financial data."""
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
    """Build the full comparison structure: metric table + radar chart data."""
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
    """Build radar chart data: normalize each metric to 0-100 across peers."""
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

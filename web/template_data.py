"""
Template data builders for FinAgent-Lithium.

Transforms pipeline AnalysisState dicts into template-safe data structures
for Jinja rendering. Separated from routes to keep routing thin.

Does NOT handle HTTP concerns (request parsing, response building).
"""

import json
import re
from typing import Optional

from markupsafe import Markup, escape


# Wind code -> company short name lookup (common lithium battery peers)
WINDCODE_NAME_MAP = {
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


def format_ind_value(val, unit: str) -> Optional[str]:
    """Format indicator value with unit, suppressing meaningless units."""
    if val is None:
        return None
    # Suppress "无" unit (means dimensionless but looks confusing)
    if not unit or unit == "无":
        return f"{val:.2f}"
    return f"{val:.2f}{unit}"


def format_peer_data(peers: list) -> list:
    """Format peer comparison data for template rendering.

    Converts raw numeric values into human-readable strings with
    appropriate scale suffixes (亿/万亿) and computes derived ratios.
    """
    if not peers:
        return []
    result = []
    for p in peers:
        wc = p.get("windcode", "")
        item = {
            "windcode": wc,
            "name": p.get("name") or WINDCODE_NAME_MAP.get(wc, wc),
            "is_current": p.get("is_current", False),
        }
        # Format large numbers for display
        # 上游可能用 net_profit/total_assets 命名（对齐 metric_config 的兜底），做键名回退
        alias = {"profit": "net_profit", "assets": "total_assets"}
        for key in ("revenue", "profit", "assets", "equity"):
            val = p.get(key)
            if val is None and key in alias:
                val = p.get(alias[key])
            if val is not None:
                if abs(val) >= 1e12:
                    item[key] = f"{val/1e12:.2f}万亿"
                elif abs(val) >= 1e8:
                    item[key] = f"{val/1e8:.0f}亿"
                else:
                    item[key] = f"{val:.0f}"
            else:
                item[key] = "-"
        # Derived ratio metrics
        rev = p.get("revenue")
        cost = p.get("cost")
        eq = p.get("equity")
        pft = p.get("profit") if p.get("profit") is not None else p.get("net_profit")
        # 仅在可计算时写 gross_margin；缺失则不设该键，模板据此隐藏整列
        if rev and cost and rev != 0:
            item["gross_margin"] = f"{(rev - cost) / rev * 100:.1f}%"
        if pft and eq and eq != 0:
            item["roe"] = f"{pft / eq * 100:.1f}%"
        else:
            item["roe"] = "-"
        result.append(item)
    return result


def render_summary_html(report_markdown: str) -> Markup:
    """Extract the '六、总结' section and render it as safe HTML.

    The markdown mixes user input and LLM free text, so it must be escaped
    before any formatting is applied -- never pipe it through `| safe` raw.
    Only a minimal subset is formatted: bold, bullet lines, paragraphs.
    """
    if "## 六、总结" not in (report_markdown or ""):
        return Markup("")
    section = report_markdown.split("## 六、总结")[1].split("---")[0]

    html_lines = []
    in_list = False
    for raw_line in section.strip().splitlines():
        line = str(escape(raw_line.strip()))
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        if line.startswith(("- ", "* ")):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{line[2:]}</li>")
            continue
        if in_list:
            html_lines.append("</ul>")
            in_list = False
        if line:
            html_lines.append(f"<p>{line}</p>")
    if in_list:
        html_lines.append("</ul>")
    return Markup("\n".join(html_lines))


def parse_json_field(raw: str) -> dict:
    """Parse a JSON field from form data, returning {} on failure."""
    if not raw or not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def build_template_data(state: dict) -> dict:
    """Build a template-safe data dict from pipeline state.

    This is the main data transformation entry point. It takes a raw
    AnalysisState dict and returns a flat dict suitable for Jinja2's
    render_template(**data) pattern.
    """

    def risk_color(risk_str: str) -> str:
        if "正常" in risk_str: return "normal"
        if "警惕" in risk_str: return "warning"
        if "高风险" in risk_str: return "danger"
        return "pending"

    def simplify_inds(indicators: dict) -> list:
        result = []
        for name, ind in indicators.items():
            val = ind.get("value")
            # RiskLevel is a (str, Enum); on Python 3.12 str(member) yields
            # 'RiskLevel.NORMAL', so prefer .value to get the '🟢正常' label.
            _rl = ind.get("risk_level", "")
            risk = _rl.value if hasattr(_rl, "value") else str(_rl)
            result.append({
                "name": name,
                "value": format_ind_value(val, ind.get('unit', '')),
                "risk_level": risk,
                "risk_color": risk_color(risk),
                "normal_range": ind.get("normal_range", ""),
                "warning_range": ind.get("warning_range", ""),
                "high_risk_range": ind.get("high_risk_range", ""),
                "explanation": ind.get("single_mapping", ind.get("explanation", "")),
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

    # ── Core ratio cards (6 items) ──
    core_ratios = _build_core_ratios(general_list, state.get("historical_data", {}))

    # ── Risk assessment panel ──
    risk_assessment = _build_risk_assessment(
        state.get("risk_summary", {}),
        anomalies,
        state.get("metric_interpretations", []),
    )

    # ── Historical trend chart data ──
    historical = state.get("historical_data", {})
    has_historical = bool(historical.get("periods"))
    chart_data = _build_chart_data(historical) if has_historical else {}

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
        "investor_summary_html": render_summary_html(state.get("report_markdown", "")),
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
        # Core ratio cards + risk assessment
        "core_ratios": core_ratios,
        "risk_assessment": risk_assessment,
        "wind_status": state.get("wind_status", {}),
        "llm_available": state.get("_llm_available", True),
        "wind_fetch_attempted": state.get("_wind_fetch_attempted", False),
        "wind_fetch_error": state.get("_wind_fetch_error", ""),
        "industry_comparison": state.get("industry_comparison", {}),
        "status": state.get("status", "unknown"),
        "wind_enriched": state.get("_wind_enriched", False),
        "wind_accounts_count": state.get("_wind_accounts_count", 0),
        "wind_classified": state.get("_wind_classified", False),
        # Peer comparison
        "peer_comparison": format_peer_data(state.get("_peer_comparison", [])),
        "has_peers": bool(state.get("_peer_comparison")),
        # Macro context
        "lithium_trend": state.get("_lithium_trend"),
        # DeepSeek macro fallback
        "deepseek_macro": state.get("_deepseek_macro", ""),
        "deepseek_macro_structured": state.get("_deepseek_macro_structured"),
        # Historical trend data
        "historical_data": historical,
        "has_historical": has_historical,
        "chart_data": chart_data,
        # AI strategy insights
        "strategy_insights": state.get("strategy_insights", {}),
        "has_strategy": bool(state.get("strategy_insights", {}).get("strategy_summary")),
        # Advanced analysis models (杜邦/CVP/现金流/Z-score/DCF/相对估值/哈佛/EVA)
        "advanced": state.get("advanced_analysis", {}),
        "has_advanced": _advanced_has_content(state.get("advanced_analysis", {})),
        # Six-dimension trend prediction
        "prediction": state.get("prediction_analysis", {}),
        "has_prediction": bool(state.get("prediction_analysis", {}).get("available")),
    }


def _advanced_has_content(advanced: dict) -> bool:
    """True if any advanced model produced usable output."""
    if not advanced:
        return False
    for section in ("business", "risk", "valuation", "strategy"):
        for model in (advanced.get(section) or {}).values():
            if isinstance(model, dict) and model.get("available"):
                return True
    return False


def _build_chart_data(historical: dict) -> dict:
    """Transform historical data into Plotly-ready chart configurations."""
    if not historical or not historical.get("periods"):
        return {}
    periods = historical["periods"]
    labels = [p.replace("年报", "") for p in periods]

    charts: dict = {}

    revenue = historical.get("metrics", {}).get("营业收入", [])
    profit = historical.get("metrics", {}).get("净利润", [])
    if revenue:
        charts["revenue_profit"] = {
            "labels": labels,
            "revenue": [round(v / 1e8, 2) if v else None for v in revenue],
            "profit": [round(v / 1e8, 2) if v else None for v in profit] if profit else [],
        }

    ratios = historical.get("derived_ratios", {})
    if ratios:
        charts["ratios"] = {
            "labels": labels,
            "series": {
                name: vals
                for name, vals in ratios.items()
                if any(v is not None for v in vals)
            },
        }

    charts["cagr"] = historical.get("cagr", {})
    charts["inflection_points"] = historical.get("inflection_points", [])

    return charts


_CORE_RATIO_NAMES = ["销售毛利率", "扣非销售净利率", "资产负债率", "流动比率", "扣非ROE", "净利润现金含量"]

_CORE_RATIO_ICONS = {
    "销售毛利率": "📊",
    "扣非销售净利率": "💰",
    "资产负债率": "⚖️",
    "流动比率": "💧",
    "扣非ROE": "🎯",
    "净利润现金含量": "💵",
}

_RATIO_TO_HISTORICAL_KEY = {
    "销售毛利率": "销售毛利率",
    "资产负债率": "资产负债率",
    "扣非ROE": "ROE",
}


def _build_core_ratios(general_list: list, historical: dict) -> list:
    lookup = {ind["name"]: ind for ind in general_list}
    derived_ratios = historical.get("derived_ratios", {})
    periods = historical.get("periods", [])
    sparkline_labels = [p.replace("年报", "") for p in periods]

    result = []
    for name in _CORE_RATIO_NAMES:
        ind = lookup.get(name)
        if not ind:
            continue
        card = {
            "name": name,
            "icon": _CORE_RATIO_ICONS.get(name, "📈"),
            "value": ind.get("value"),
            "risk_level": ind.get("risk_level", ""),
            "risk_color": ind.get("risk_color", "pending"),
            "normal_range": ind.get("normal_range", ""),
            "formula": ind.get("formula", ""),
            "sparkline": None,
        }
        hist_key = _RATIO_TO_HISTORICAL_KEY.get(name)
        if hist_key and hist_key in derived_ratios:
            vals = derived_ratios[hist_key]
            if any(v is not None for v in vals):
                card["sparkline"] = {"labels": sparkline_labels, "values": vals}
        result.append(card)
    return result


def _build_risk_assessment(
    risk_summary: dict,
    anomalies: list,
    metric_interpretations: list,
) -> dict:
    level = risk_summary.get("level", "unknown")
    level_badge = {
        "stable": ("🟢", "稳健"),
        "watch": ("🟡", "关注"),
        "high_risk": ("🔴", "高风险"),
        "anomaly_detected": ("🔴", "异常检出"),
    }.get(level, ("⚪", "未知"))

    high_risk_items = [
        item for item in metric_interpretations
        if item.get("status") == "high_risk"
    ]
    warning_items = [
        item for item in metric_interpretations
        if item.get("status") == "warning"
    ]

    return {
        "level": level,
        "badge_icon": level_badge[0],
        "badge_text": level_badge[1],
        "summary": risk_summary.get("summary", ""),
        "anomaly_count": len(anomalies),
        "high_risk_count": len(high_risk_items),
        "warning_count": len(warning_items),
        "anomalies": anomalies,
        "high_risk_items": high_risk_items,
        "warning_items": warning_items,
    }

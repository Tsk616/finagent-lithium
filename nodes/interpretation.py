"""
Structured interpretation helpers for FinAgent-Lithium.

The module turns calculated metrics, macro context, anomaly signals, and peer
benchmarks into evidence-backed explanation objects for UI/report rendering.
LLM output can enhance the language, but fallback output must never invent data.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_structured_interpretations(
    general_indicators: Optional[Dict[str, Dict[str, Any]]] = None,
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]] = None,
    anomaly_signals: Optional[List[Dict[str, Any]]] = None,
    macro_context: Optional[Dict[str, Any]] = None,
    industry_comparison: Optional[Dict[str, Any]] = None,
    weighted_score: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build structured interpretations from already-computed evidence."""
    all_metrics = _flatten_metrics(general_indicators or {}, sector_indicators or {})
    metric_interpretations = [_interpret_metric(name, ind) for name, ind in all_metrics.items()]

    macro_insights = _interpret_macro_context(macro_context or {})
    industry_insights = _interpret_industry_comparison(industry_comparison or {})
    model_status = {
        "enabled": ["metric_interpretation", "macro_context", "industry_benchmark"],
        "skipped": [],
    }
    if not macro_insights["available"]:
        model_status["skipped"].append("macro_context")
    if not industry_insights["available"]:
        model_status["skipped"].append("industry_benchmark")

    risk_summary = _build_risk_summary(metric_interpretations, anomaly_signals or [], weighted_score or {})

    return {
        "metric_interpretations": metric_interpretations,
        "macro_insights": macro_insights,
        "industry_benchmark_insights": industry_insights,
        "risk_summary": risk_summary,
        "analysis_models": model_status,
    }


def _flatten_metrics(
    general_indicators: Dict[str, Dict[str, Any]],
    sector_indicators: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for name, ind in general_indicators.items():
        result[name] = dict(ind)
    for _sector_label, categories in sector_indicators.items():
        for _category, indicators in categories.items():
            for name, ind in indicators.items():
                result[name] = dict(ind)
    return result


def _metric_name(name: str, ind: Dict[str, Any]) -> str:
    return str(ind.get("display_name") or ind.get("canonical_name") or name)


def _interpret_metric(name: str, ind: Dict[str, Any]) -> Dict[str, Any]:
    display_name = _metric_name(name, ind)
    value = ind.get("value")
    unit = ind.get("unit") or ""
    risk = _risk_text(ind.get("risk_level"))
    category = ind.get("metric_type") or ind.get("metric_category") or ""
    source_explanation = ind.get("single_mapping") or ind.get("explanation") or ""

    if value is None:
        return {
            "metric": display_name,
            "category": category,
            "status": "missing_data",
            "value": None,
            "unit": unit,
            "risk_level": risk,
            "interpretation": f"{display_name} has insufficient data, so no conclusion is generated.",
            "evidence": {},
        }

    formatted_value = _format_value(value, unit)
    status = "normal"
    if _contains_any(risk, ["高", "danger", "high"]):
        status = "high_risk"
    elif _contains_any(risk, ["警", "warning", "risk"]):
        status = "warning"

    if source_explanation:
        interpretation = f"{display_name} is {formatted_value}. {source_explanation}"
    else:
        interpretation = f"{display_name} is {formatted_value}; the conclusion is based only on this calculated value."

    return {
        "metric": display_name,
        "category": category,
        "status": status,
        "value": value,
        "unit": unit,
        "risk_level": risk,
        "interpretation": interpretation,
        "evidence": {display_name: value},
    }


def _interpret_macro_context(macro_context: Dict[str, Any]) -> Dict[str, Any]:
    if not macro_context:
        return {
            "available": False,
            "summary": "No macro data available.",
            "points": [],
        }

    trend = macro_context.get("lithium_trend") if isinstance(macro_context, dict) else None
    if not trend and {"current", "change_3m_pct"}.intersection(macro_context.keys()):
        trend = macro_context

    points: List[str] = []
    if isinstance(trend, dict):
        current = trend.get("current")
        unit = trend.get("unit", "")
        change = trend.get("change_3m_pct")
        direction = trend.get("trend")
        if current is not None:
            points.append(f"Lithium price latest value is {_format_value(current, unit)}.")
        if change is not None:
            points.append(f"Three-month change is {_format_value(change, '%')}.")
        if direction:
            points.append(f"Trend direction: {direction}.")

    if not points:
        return {
            "available": False,
            "summary": "Macro data is present but insufficient for interpretation.",
            "points": [],
        }

    return {
        "available": True,
        "summary": " ".join(points),
        "points": points,
    }


def _interpret_industry_comparison(industry_comparison: Dict[str, Any]) -> Dict[str, Any]:
    comparisons = industry_comparison.get("comparisons") or []
    if not industry_comparison.get("available") or not comparisons:
        return {
            "available": False,
            "summary": industry_comparison.get("reason") or "No benchmark data available.",
            "points": [],
        }

    points = []
    for row in comparisons[:5]:
        metric = row.get("metric")
        relation = row.get("relation")
        current = row.get("current_value")
        average = row.get("peer_average")
        if not metric:
            continue
        if relation == "better_than_industry":
            label = "better than"
        elif relation == "worse_than_industry":
            label = "worse than"
        else:
            label = "close to"
        points.append(f"{metric} is {label} industry average ({current} vs {average}).")

    return {
        "available": True,
        "summary": " ".join(points) if points else "Benchmark data is available but not interpretable.",
        "points": points,
    }


def _build_risk_summary(
    metric_interpretations: List[Dict[str, Any]],
    anomaly_signals: List[Dict[str, Any]],
    weighted_score: Dict[str, Any],
) -> Dict[str, Any]:
    high_risk_count = sum(1 for item in metric_interpretations if item.get("status") == "high_risk")
    warning_count = sum(1 for item in metric_interpretations if item.get("status") == "warning")
    score = weighted_score.get("score")

    if anomaly_signals:
        level = "anomaly_detected"
        summary = f"{len(anomaly_signals)} anomaly signal(s) were detected."
    elif high_risk_count:
        level = "high_risk"
        summary = f"{high_risk_count} high-risk metric(s) require attention."
    elif warning_count:
        level = "watch"
        summary = f"{warning_count} warning metric(s) should be monitored."
    else:
        level = "stable"
        summary = "No obvious anomaly signal was detected."

    if score is not None:
        summary = f"Weighted score is {score}/100. {summary}"

    return {
        "level": level,
        "summary": summary,
        "anomaly_count": len(anomaly_signals),
        "high_risk_metric_count": high_risk_count,
        "warning_metric_count": warning_count,
    }


def _contains_any(text: str, needles: List[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _risk_text(risk_level: Any) -> str:
    if risk_level is None:
        return ""
    if hasattr(risk_level, "value"):
        return str(risk_level.value)
    return str(risk_level)


def _format_value(value: Any, unit: str) -> str:
    if isinstance(value, (int, float)):
        formatted = f"{value:.2f}"
    else:
        formatted = str(value)
    return f"{formatted}{unit}" if unit else formatted

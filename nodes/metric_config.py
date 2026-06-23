"""
Metric governance helpers.

Loads business-owned metric weights from metric_weight_config.json, resolves
small naming differences, enriches computed indicator results, and calculates
an explainable weighted score.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "metric_weight_config.json"


def _normalize_metric_name(name: str) -> str:
    """Normalize metric names for alias matching without changing display text."""
    if not name:
        return ""
    return re.sub(r"[\s（）()\-/／、:_：]+", "", str(name)).lower()


@lru_cache(maxsize=4)
def load_metric_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and validate the metric weight config."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    _validate_metric_config(config)
    return config


def _validate_metric_config(config: Dict[str, Any]) -> None:
    sectors = config.get("sectors")
    if not isinstance(sectors, dict) or not sectors:
        raise ValueError("metric config must contain non-empty sectors")

    for sector_code, sector in sectors.items():
        metrics = sector.get("metrics", [])
        if not isinstance(metrics, list) or not metrics:
            raise ValueError(f"sector {sector_code} must contain metrics")
        for metric in metrics:
            name = metric.get("display_name") or metric.get("canonical_name")
            if not name:
                raise ValueError(f"sector {sector_code} has metric without name")
            try:
                weight = float(metric.get("weight"))
            except (TypeError, ValueError):
                raise ValueError(f"metric {name} in sector {sector_code} has invalid weight")
            if weight < 0:
                raise ValueError(f"metric {name} in sector {sector_code} has negative weight")
            metric["weight"] = weight


def find_metric_config(sector_code: Optional[str], metric_name: str) -> Optional[Dict[str, Any]]:
    """Find one metric config entry by sector and metric name."""
    if not sector_code or not metric_name:
        return None

    config = load_metric_config()
    sector = config.get("sectors", {}).get(sector_code)
    if not sector:
        return None

    target = _normalize_metric_name(metric_name)
    if not target:
        return None

    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for metric in sector.get("metrics", []):
        names = [
            metric.get("canonical_name", ""),
            metric.get("display_name", ""),
            *metric.get("aliases", []),
        ]
        normalized = [_normalize_metric_name(n) for n in names if n]
        if target in normalized:
            return dict(metric)
        for idx, item in enumerate(normalized):
            if item and (target in item or item in target):
                candidates.append((idx, metric))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return dict(candidates[0][1])
    return None


def enrich_metric_results(
    sector_code: Optional[str],
    indicators: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Attach canonical naming, category, and weight metadata to flat indicators."""
    enriched: Dict[str, Dict[str, Any]] = {}
    for name, indicator in (indicators or {}).items():
        item = dict(indicator)
        metric = find_metric_config(sector_code, name)
        if metric:
            item.update({
                "canonical_name": metric.get("canonical_name"),
                "display_name": metric.get("display_name"),
                "metric_category": metric.get("metric_category"),
                "metric_type": metric.get("metric_type"),
                "weight": metric.get("weight"),
                "weight_logic": metric.get("weight_logic", ""),
            })
        else:
            item.setdefault("canonical_name", name)
            item.setdefault("display_name", name)
        enriched[name] = item
    return enriched


def enrich_sector_indicator_results(
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]],
) -> Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]:
    """Attach metric config metadata to nested sector indicator results."""
    enriched: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for sector_label, categories in (sector_indicators or {}).items():
        enriched_categories: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for category_name, indicators in categories.items():
            enriched_indicators = {}
            for name, indicator in indicators.items():
                sector_code = indicator.get("_sector_code") or _extract_sector_code(sector_label)
                enriched_indicators[name] = enrich_metric_results(
                    sector_code, {name: indicator}
                )[name]
            enriched_categories[category_name] = enriched_indicators
        enriched[sector_label] = enriched_categories
    return enriched


def flatten_indicator_results(
    general_indicators: Optional[Dict[str, Dict[str, Any]]] = None,
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, Dict[str, Any]]]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Flatten general and sector indicators into a single canonical lookup."""
    flattened: Dict[str, Dict[str, Any]] = {}

    for name, indicator in (general_indicators or {}).items():
        key = indicator.get("canonical_name") or name
        flattened[key] = dict(indicator)

    for categories in (sector_indicators or {}).values():
        for indicators in categories.values():
            for name, indicator in indicators.items():
                key = indicator.get("canonical_name") or name
                if key not in flattened:
                    flattened[key] = dict(indicator)

    return flattened


def calculate_weighted_score(indicators: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate weighted score, normalizing over computable weighted indicators."""
    components = []
    skipped = []

    for name, indicator in (indicators or {}).items():
        weight = indicator.get("weight")
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue

        score = _risk_to_score(indicator.get("risk_level"))
        component = {
            "name": indicator.get("display_name") or indicator.get("canonical_name") or name,
            "canonical_name": indicator.get("canonical_name") or name,
            "value": indicator.get("value"),
            "unit": indicator.get("unit", ""),
            "risk_level": str(indicator.get("risk_level", "")),
            "weight": weight,
            "metric_category": indicator.get("metric_category"),
            "metric_type": indicator.get("metric_type"),
            "weight_logic": indicator.get("weight_logic", ""),
        }

        if indicator.get("value") is None or score is None:
            skipped.append(component)
            continue

        component["score"] = score
        component["weighted_score"] = score * weight
        components.append(component)

    available_weight = sum(c["weight"] for c in components)
    skipped_weight = sum(c["weight"] for c in skipped)
    if available_weight <= 0:
        final_score = None
    else:
        final_score = round(sum(c["weighted_score"] for c in components) / available_weight, 2)

    return {
        "score": final_score,
        "available_weight": round(available_weight, 2),
        "skipped_weight": round(skipped_weight, 2),
        "components": components,
        "skipped_components": skipped,
        "method": "risk_level_weighted_normalized",
    }


def build_industry_comparison(
    current_indicators: Dict[str, Dict[str, Any]],
    peer_financials: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Build industry comparison from supplied peer financials only."""
    if not peer_financials:
        return {
            "available": False,
            "reason": "No peer benchmark data available",
            "comparisons": [],
        }

    comparisons = []
    for name, indicator in (current_indicators or {}).items():
        current_value = indicator.get("value")
        if current_value is None:
            continue

        canonical = indicator.get("canonical_name") or name
        peer_values = _derive_peer_metric_values(canonical, peer_financials)
        if not peer_values:
            continue

        peer_average = round(sum(peer_values) / len(peer_values), 2)
        current_value = round(float(current_value), 2)
        direction = _comparison_direction(canonical)
        delta = round(current_value - peer_average, 2)

        if abs(delta) <= max(abs(peer_average) * 0.03, 0.01):
            relation = "near_industry"
        elif direction == "lower_better":
            relation = "better_than_industry" if current_value < peer_average else "worse_than_industry"
        else:
            relation = "better_than_industry" if current_value > peer_average else "worse_than_industry"

        comparisons.append({
            "metric": indicator.get("display_name") or canonical,
            "canonical_name": canonical,
            "current_value": current_value,
            "peer_average": peer_average,
            "delta": delta,
            "relation": relation,
            "peer_count": len(peer_values),
            "source": "peer_financials",
            "weight": indicator.get("weight"),
        })

    if not comparisons:
        return {
            "available": False,
            "reason": "No comparable metrics can be derived from peer benchmark data",
            "comparisons": [],
        }

    comparisons.sort(key=lambda row: float(row.get("weight") or 0), reverse=True)
    return {
        "available": True,
        "reason": "",
        "comparisons": comparisons,
    }


def _derive_peer_metric_values(metric_name: str, peers: Iterable[Dict[str, Any]]) -> List[float]:
    normalized = _normalize_metric_name(metric_name)
    values = []
    for peer in peers:
        value = None
        revenue = _num(peer.get("revenue"))
        profit = _num(peer.get("profit") if peer.get("profit") is not None else peer.get("net_profit"))
        assets = _num(peer.get("assets") if peer.get("assets") is not None else peer.get("total_assets"))
        equity = _num(peer.get("equity"))

        if normalized == _normalize_metric_name("资产负债率"):
            if assets and equity is not None:
                value = (assets - equity) / assets * 100
        elif normalized in {
            _normalize_metric_name("扣非 ROE"),
            _normalize_metric_name("扣非ROE"),
        }:
            if profit is not None and equity:
                value = profit / equity * 100
        elif normalized == _normalize_metric_name("扣非销售净利率"):
            if profit is not None and revenue:
                value = profit / revenue * 100

        if value is not None:
            values.append(round(value, 2))
    return values


def _comparison_direction(metric_name: str) -> str:
    normalized = _normalize_metric_name(metric_name)
    lower_better = {
        _normalize_metric_name("资产负债率"),
        _normalize_metric_name("期间费用率"),
        _normalize_metric_name("现金周转周期"),
    }
    return "lower_better" if normalized in lower_better else "higher_better"


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _risk_to_score(risk_level: Any) -> Optional[float]:
    text = str(risk_level or "")
    if "待补充" in text or "PENDING" in text:
        return None
    if "高风险" in text or "HIGH_RISK" in text:
        return 20.0
    if "警惕" in text or "WARNING" in text:
        return 60.0
    if "正常" in text or "NORMAL" in text:
        return 100.0
    return None


def _extract_sector_code(sector_label: str) -> Optional[str]:
    if not sector_label:
        return None
    match = re.match(r"^(\d+\.\d+)", str(sector_label).strip())
    return match.group(1) if match else None


def summarize_metric_config(sector_code: Optional[str]) -> Dict[str, Any]:
    """Return compact config stats for API/template display."""
    config = load_metric_config()
    sector = config.get("sectors", {}).get(sector_code or "")
    if not sector:
        return {"available": False, "sector_code": sector_code, "metrics": 0}
    metrics = sector.get("metrics", [])
    return {
        "available": True,
        "sector_code": sector_code,
        "sector_name": sector.get("name", ""),
        "metrics": len(metrics),
        "total_weight": round(sum(float(m.get("weight", 0)) for m in metrics), 2),
        "normalization": config.get("normalization", ""),
    }

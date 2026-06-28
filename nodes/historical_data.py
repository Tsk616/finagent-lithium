"""
Historical financial data fetcher and trend analytics.

Fetches 3-5 years of annual financial data via Wind MCP / MX Data,
computes CAGR and detects trend inflection points.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

_KEY_METRICS = [
    "营业收入", "营业成本", "净利润", "总资产", "总负债", "净资产",
    "经营活动现金流净额", "研发费用", "扣非净利润",
    # Extra balance-sheet items for liquidity/turnover trend series (same Wind
    # call, just more keys) — feeds the six-dimension prediction engine.
    "流动资产", "流动负债", "存货", "应收账款", "固定资产",
]


def _derive_period_labels(current_period: str, years: int = 5) -> List[str]:
    m = re.match(r"(\d{4})", current_period or "")
    if not m:
        return []
    end_year = int(m.group(1))
    return [f"{y}年报" for y in range(end_year - years + 1, end_year + 1)]


def _compute_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """Plain ratio (not a percentage), e.g. turnover times / current ratio."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, 2)


def _derive_ratios(metrics: Dict[str, List[Optional[float]]], n: int) -> Dict[str, List[Optional[float]]]:
    revenue = metrics.get("营业收入", [None] * n)
    cost = metrics.get("营业成本", [None] * n)
    profit = metrics.get("净利润", [None] * n)
    assets = metrics.get("总资产", [None] * n)
    liabilities = metrics.get("总负债", [None] * n)
    equity = metrics.get("净资产", [None] * n)
    cur_assets = metrics.get("流动资产", [None] * n)
    cur_liab = metrics.get("流动负债", [None] * n)
    inventory = metrics.get("存货", [None] * n)
    receivables = metrics.get("应收账款", [None] * n)
    fixed_assets = metrics.get("固定资产", [None] * n)

    def col(seq, i):
        return seq[i] if i < len(seq) else None

    return {
        "销售毛利率": [
            _compute_ratio((revenue[i] or 0) - (cost[i] or 0), revenue[i])
            if col(revenue, i) is not None and col(cost, i) is not None else None
            for i in range(n)
        ],
        "资产负债率": [_compute_ratio(col(liabilities, i), col(assets, i)) for i in range(n)],
        "净利率": [_compute_ratio(col(profit, i), col(revenue, i)) for i in range(n)],
        "ROE": [_compute_ratio(col(profit, i), col(equity, i)) for i in range(n)],
        # Liquidity / turnover series (plain ratios) for the prediction engine.
        "流动比率": [_ratio(col(cur_assets, i), col(cur_liab, i)) for i in range(n)],
        "速动比率": [
            _ratio((col(cur_assets, i) or 0) - (col(inventory, i) or 0), col(cur_liab, i))
            if col(cur_assets, i) is not None and col(cur_liab, i) is not None else None
            for i in range(n)
        ],
        "存货周转率": [_ratio(col(cost, i), col(inventory, i)) for i in range(n)],
        "应收账款周转率": [_ratio(col(revenue, i), col(receivables, i)) for i in range(n)],
        "固定资产周转率": [_ratio(col(revenue, i), col(fixed_assets, i)) for i in range(n)],
    }


def compute_cagr(values: List[Optional[float]], years: List[str]) -> Optional[Dict[str, Any]]:
    paired = [(y, v) for y, v in zip(years, values) if v is not None and v > 0]
    if len(paired) < 2:
        return None
    start_year, start_val = paired[0]
    end_year, end_val = paired[-1]
    m_start = re.match(r"(\d{4})", start_year)
    m_end = re.match(r"(\d{4})", end_year)
    if not m_start or not m_end:
        return None
    n = int(m_end.group(1)) - int(m_start.group(1))
    if n <= 0:
        return None
    cagr_val = ((end_val / start_val) ** (1 / n) - 1) * 100
    return {
        "value": round(cagr_val, 1),
        "years": n,
        "start_value": start_val,
        "end_value": end_val,
    }


def detect_inflection_points(
    metric_name: str,
    values: List[Optional[float]],
    periods: List[str],
) -> List[Dict[str, Any]]:
    growth_rates = []
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if prev is not None and cur is not None and prev != 0:
            growth_rates.append(((cur - prev) / abs(prev)) * 100)
        else:
            growth_rates.append(None)

    points = []
    for i in range(1, len(growth_rates)):
        prev_g, cur_g = growth_rates[i - 1], growth_rates[i]
        if prev_g is None or cur_g is None:
            continue

        inflection_type = None
        if prev_g >= 0 and cur_g < 0:
            inflection_type = "sign_reversal"
            desc = f"{metric_name}增速由正转负（{prev_g:+.1f}% → {cur_g:+.1f}%），增长拐点出现"
        elif prev_g < 0 and cur_g >= 0:
            inflection_type = "recovery"
            desc = f"{metric_name}增速由负转正（{prev_g:+.1f}% → {cur_g:+.1f}%），出现回暖信号"
        elif abs(cur_g - prev_g) > 20 and cur_g < prev_g:
            inflection_type = "deceleration"
            desc = f"{metric_name}增速大幅放缓（{prev_g:+.1f}% → {cur_g:+.1f}%）"
        elif abs(cur_g - prev_g) > 20 and cur_g > prev_g:
            inflection_type = "acceleration"
            desc = f"{metric_name}增速大幅提升（{prev_g:+.1f}% → {cur_g:+.1f}%）"

        if inflection_type:
            period_idx = i + 1
            points.append({
                "metric": metric_name,
                "period": periods[period_idx] if period_idx < len(periods) else "",
                "type": inflection_type,
                "prior_growth": round(prev_g, 1),
                "current_growth": round(cur_g, 1),
                "description": desc,
            })

    return points


def fetch_historical_periods(
    windcode: str,
    current_period: str,
    years: int = 5,
) -> Dict[str, Any]:
    periods = _derive_period_labels(current_period, years)
    if not periods:
        return {}

    from nodes.wind_adapter import fetch_financials

    raw_data: Dict[str, Dict[str, Optional[float]]] = {}
    failed_periods: List[str] = []

    def _fetch_one(period: str):
        try:
            return period, fetch_financials(windcode, period=period, timeout=15)
        except Exception as e:
            logger.warning("Historical fetch failed for %s %s: %s", windcode, period, e)
            return period, None

    max_workers = min(len(periods), 3)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, p): p for p in periods}
        for future in as_completed(futures):
            period, data = future.result()
            if data and len(data) >= 3:
                raw_data[period] = data
            else:
                failed_periods.append(period)

    if not raw_data:
        return {}

    ordered_periods = [p for p in periods if p in raw_data]
    n = len(ordered_periods)

    metrics: Dict[str, List[Optional[float]]] = {}
    for metric in _KEY_METRICS:
        metrics[metric] = [raw_data[p].get(metric) for p in ordered_periods]

    derived_ratios = _derive_ratios(metrics, n)

    cagr: Dict[str, Dict[str, Any]] = {}
    for metric in ["营业收入", "净利润", "总资产", "净资产"]:
        vals = metrics.get(metric, [])
        result = compute_cagr(vals, ordered_periods)
        if result:
            cagr[metric] = result

    inflection_points: List[Dict[str, Any]] = []
    for metric in ["营业收入", "净利润"]:
        vals = metrics.get(metric, [])
        inflection_points.extend(detect_inflection_points(metric, vals, ordered_periods))

    return {
        "periods": ordered_periods,
        "metrics": metrics,
        "derived_ratios": derived_ratios,
        "cagr": cagr,
        "inflection_points": inflection_points,
        "fetch_status": {
            "total_periods": len(periods),
            "fetched": len(raw_data),
            "failed_periods": failed_periods,
        },
    }

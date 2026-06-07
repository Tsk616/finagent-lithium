"""
Node 3: calculate_general — 通用指标计算

Computes 11 general financial indicators:
  8 computable (no prior-period data needed):
    - 销售毛利率, 扣非销售净利率, 净利润现金含量, 资产负债率,
      流动比率, 速动比率, 期间费用率, 扣非ROE
  3 pending (need prior-period data, MVP: mark as 待补充):
    - 营收同比增速, 应收账款同比增速, 存货同比增速

All computation is pure Python. Threshold judgment uses spec-defined ranges
with boundary values assigned to the more conservative (higher-risk) side.
"""

import re
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum


class RiskLevel(str, Enum):
    NORMAL = "🟢正常"
    WARNING = "🟡警惕"
    HIGH_RISK = "🔴高风险"
    PENDING = "⚪待补充"


# ── Threshold types for parsing ──────────────────────────────────────

def _parse_threshold(raw: str) -> Optional[Dict[str, Any]]:
    """Parse a threshold string like '>=1.2', '5~9', '<4', '30%~55%' into a dict.

    Returns None for unparseable strings (benchmark-dependent).
    """
    if not raw:
        return None
    s = raw.strip()
    # Remove trailing unit suffixes (%，次，天，倍，无, pct)
    s = re.sub(r'[次天倍%pct\s]+$', '', s)
    s = re.sub(r'\s+', '', s)

    # >=X
    if m := re.match(r'^>=([\d.]+)$', s):
        return {"type": "gte", "value": float(m.group(1))}
    # >X
    if m := re.match(r'^[＞>]([\d.]+)$', s):
        return {"type": "gt", "value": float(m.group(1))}
    # <=X
    if m := re.match(r'^<=([\d.]+)$', s):
        return {"type": "lte", "value": float(m.group(1))}
    # <X or ＜X
    if m := re.match(r'^[＜<]([\d.]+)$', s):
        return {"type": "lt", "value": float(m.group(1))}
    # X~Y or X%~Y%
    if m := re.match(r'^([\d.]+)[%~]?\s*~\s*([\d.]+)', s):
        return {"type": "range", "low": float(m.group(1)), "high": float(m.group(2))}
    # X-Y
    if m := re.match(r'^([\d.]+)-([\d.]+)$', s):
        return {"type": "range", "low": float(m.group(1)), "high": float(m.group(2))}

    return None


def _value_in_interval(val: float, parsed: Dict[str, Any]) -> bool:
    """Check if val falls within the parsed threshold interval.

    Boundary rule: boundary values are EXCLUDED from normal/warning and
    assigned to the higher-risk tier (more conservative).
    - normal: open on the warning side
    - warning: closed on normal side, open on high_risk side
    - high_risk: closed on warning side
    """
    t = parsed["type"]
    if t == "gte":
        return val >= parsed["value"]
    elif t == "gt":
        return val > parsed["value"]
    elif t == "lte":
        return val <= parsed["value"]
    elif t == "lt":
        return val < parsed["value"]
    elif t == "range":
        return parsed["low"] <= val <= parsed["high"]
    return False


def judge_risk_level(value: Optional[float],
                     normal_raw: str, warning_raw: str, high_risk_raw: str,
                     is_pending: bool = False) -> Tuple[RiskLevel, str]:
    """Determine risk level for a computed indicator value.

    Args:
        value: Computed indicator value (None if data missing).
        normal_raw, warning_raw, high_risk_raw: Threshold strings.
        is_pending: True for indicators that need prior-period data.

    Returns:
        (RiskLevel, threshold_range_string for display)
    """
    if is_pending and value is None:
        return RiskLevel.PENDING, "上期数据缺失"

    if value is None:
        return RiskLevel.PENDING, "数据缺失"

    # Try parsing thresholds
    n_parsed = _parse_threshold(normal_raw)
    w_parsed = _parse_threshold(warning_raw)
    h_parsed = _parse_threshold(high_risk_raw)

    # Check from safest to riskiest
    if n_parsed and _value_in_interval(value, n_parsed):
        return RiskLevel.NORMAL, normal_raw
    if w_parsed and _value_in_interval(value, w_parsed):
        return RiskLevel.WARNING, warning_raw
    if h_parsed and _value_in_interval(value, h_parsed):
        return RiskLevel.HIGH_RISK, high_risk_raw

    # Value falls outside all parsed ranges. Infer direction.
    better_higher = _infer_higher_better(n_parsed, w_parsed, h_parsed)

    if better_higher:
        if n_parsed and _get_max(n_parsed) is not None and value > _get_max(n_parsed):
            return RiskLevel.NORMAL, normal_raw
        if h_parsed and _get_min(h_parsed) is not None and value < _get_min(h_parsed):
            return RiskLevel.HIGH_RISK, high_risk_raw
    else:
        if n_parsed and _get_min(n_parsed) is not None and value < _get_min(n_parsed):
            return RiskLevel.NORMAL, normal_raw
        if h_parsed and _get_max(h_parsed) is not None and value > _get_max(h_parsed):
            return RiskLevel.HIGH_RISK, high_risk_raw

    return RiskLevel.WARNING, f"无法判定 ({normal_raw})"


def _infer_higher_better(n_parsed, w_parsed, h_parsed) -> bool:
    """Infer direction: True if higher values are better (e.g. margins, turnover)."""
    if h_parsed and h_parsed.get("type") in ("lt", "lte"):
        return True
    if h_parsed and h_parsed.get("type") in ("gt", "gte"):
        return False
    if n_parsed and n_parsed.get("type") in ("gte", "gt"):
        return True
    if n_parsed and n_parsed.get("type") in ("lte", "lt"):
        return False
    # Warning is a single-sided threshold: if >X triggers warning, higher is worse
    if w_parsed and n_parsed and n_parsed.get("type") == "range":
        if w_parsed.get("type") in ("gt", "gte") and w_parsed.get("value", 0) >= n_parsed.get("high", 0):
            return False  # exceeding upper bound is bad
        if w_parsed.get("type") in ("lt", "lte") and w_parsed.get("value", 0) <= n_parsed.get("low", 0):
            return True   # going below lower bound is bad
    if (n_parsed and n_parsed.get("type") == "range"
            and w_parsed and w_parsed.get("type") == "range"):
        if w_parsed.get("high", 0) <= n_parsed.get("low", 0):
            return True
        if w_parsed.get("low", 0) >= n_parsed.get("high", 0):
            return False
    return True


def _get_max(parsed) -> Optional[float]:
    if not parsed: return None
    t = parsed.get("type")
    if t == "range": return parsed.get("high")
    if t in ("gte", "gt"): return float("inf")
    if t in ("lte", "lt"): return parsed.get("value")
    return None


def _get_min(parsed) -> Optional[float]:
    if not parsed: return None
    t = parsed.get("type")
    if t == "range": return parsed.get("low")
    if t in ("lte", "lt"): return float("-inf")
    if t in ("gte", "gt"): return parsed.get("value")
    return None


def _fmt_value(value: Optional[float]) -> Optional[float]:
    """Round value to 2 decimal places."""
    if value is None:
        return None
    return round(value, 2)


def _get_val_strict(data: Dict[str, Optional[float]], *keys: str) -> Optional[float]:
    """Get first non-None value from data matching any of the exact keys (no fuzzy fallback)."""
    for k in keys:
        v = data.get(k)
        if v is not None:
            return v
    return None


def _get_val(data: Dict[str, Optional[float]], *keys: str) -> Optional[float]:
    """Get first non-None value from data matching any of the given keys.

    Uses exact match first, then falls back to fuzzy substring matching.
    """
    # Exact match first
    for k in keys:
        v = data.get(k)
        if v is not None:
            return v
    # Fuzzy: check if any key partially matches
    for dk, dv in data.items():
        if dv is not None:
            for k in keys:
                if k in dk or dk in k:
                    return dv
    return None


# ── Indicator definitions ─────────────────────────────────────────────
# Each entry: (name, formula_lambda, unit, normal, warning, high_risk,
#              single_mapping, accounts_list, is_pending)

def _needs(data, *keys):
    """Return True only if ALL keys are present with non-None values in data."""
    return all(data.get(k) is not None for k in keys)

def _compute_yoy(
    data: Dict[str, Optional[float]],
    current_key: str,
    *prior_keys: str,
) -> Optional[float]:
    """Compute year-over-year growth rate using strict key matching."""
    current = data.get(current_key)
    prior = None
    for k in prior_keys:
        prior = data.get(k)
        if prior is not None:
            break
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / prior * 100


def _build_general_indicator_specs() -> List[Dict[str, Any]]:
    """Return the 11 general indicator definitions."""
    return [
        {
            "name": "销售毛利率",
            "unit": "%",
            "accounts": ["营业收入", "营业成本"],
            "compute": lambda d: (
                (d.get("营业收入", 0) - d.get("营业成本", 0)) / d.get("营业收入", 1) * 100
            ) if d.get("营业收入") and d.get("营业收入") != 0 else None,
            "normal_range": ">=20",
            "warning_range": "10~20",
            "high_risk_range": "<10",
            "single_mapping": "反映产品基础盈利能力。毛利率持续下行 = 行业供需恶化、原材料涨价或行业价格战；单边异常暴涨 = 大宗商品涨价红利或产品结构大幅优化。",
            "is_pending": False,
        },
        {
            "name": "扣非销售净利率",
            "unit": "%",
            "accounts": ["扣非净利润", "营业收入"],
            "compute": lambda d: (
                d.get("扣非净利润") / d.get("营业收入", 1) * 100
            ) if _needs(d, "营业收入", "扣非净利润") and d.get("营业收入") != 0 else None,
            "normal_range": ">=5",
            "warning_range": "2~5",
            "high_risk_range": "<2",
            "single_mapping": "剔除政府补助、资产处置等一次性收益，反映主业真实赚钱能力。净利率下滑 = 主业经营恶化，盈利依赖非经常性收益。",
            "is_pending": False,
        },
        {
            "name": "净利润现金含量",
            "unit": "无",
            "accounts": ["经营活动现金流净额", "扣非净利润"],
            "compute": lambda d: (
                d.get("经营活动现金流净额") / d.get("扣非净利润", 1)
            ) if _needs(d, "经营活动现金流净额", "扣非净利润") and d.get("扣非净利润") != 0 else None,
            "normal_range": ">=1.0",
            "warning_range": "0.7~1.0",
            "high_risk_range": "<0.7",
            "single_mapping": "衡量盈利含金量。>1 代表利润全部落地为现金；<0.7 代表利润沉淀在应收、存货，账面盈利无现金流支撑。",
            "is_pending": False,
        },
        {
            "name": "资产负债率",
            "unit": "%",
            "accounts": ["总负债", "总资产"],
            "compute": lambda d: (
                d.get("总负债") / d.get("总资产", 1) * 100
            ) if _needs(d, "总资产", "总负债") and d.get("总资产") != 0 else None,
            "normal_range": "30%~55%",
            "warning_range": "55%~70%",
            "high_risk_range": ">70%",
            "single_mapping": "衡量整体负债压力与杠杆水平。偏高代表企业靠负债扩产，偿债压力攀升。",
            "is_pending": False,
        },
        {
            "name": "流动比率",
            "unit": "无",
            "accounts": ["流动资产", "流动负债"],
            "compute": lambda d: (
                d.get("流动资产") / d.get("流动负债", 1)
            ) if _needs(d, "流动资产", "流动负债") and d.get("流动负债") != 0 else None,
            "normal_range": ">=1.2",
            "warning_range": "1.0~1.2",
            "high_risk_range": "<1.0",
            "single_mapping": "短期偿债核心指标。低于1代表流动资产无法覆盖短期刚性负债，短期偿债承压。",
            "is_pending": False,
        },
        {
            "name": "速动比率",
            "unit": "无",
            "accounts": ["流动资产", "存货", "预付款项", "流动负债"],
            "compute": lambda d: (
                (d.get("流动资产") - (d.get("存货") or 0) - (d.get("预付款项") or 0))
                / d.get("流动负债", 1)
            ) if _needs(d, "流动资产", "流动负债") and d.get("流动负债") != 0 else None,
            "normal_range": ">=0.8",
            "warning_range": "0.6~0.8",
            "high_risk_range": "<0.6",
            "single_mapping": "剔除变现难度高的存货，精准反映即时偿债能力。受存货积压扰动更小。",
            "is_pending": False,
        },
        {
            "name": "期间费用率",
            "unit": "%",
            "accounts": ["销售费用", "管理费用", "财务费用", "研发费用", "营业收入"],
            "compute": lambda d: (
                ((d.get("销售费用") or 0) + (d.get("管理费用") or 0)
                 + (d.get("财务费用") or 0) + (d.get("研发费用") or 0))
                / d.get("营业收入", 1) * 100
            ) if _needs(d, "营业收入") and d.get("营业收入") != 0 else None,
            "normal_range": "<15%",
            "warning_range": "15%~25%",
            "high_risk_range": ">25%",
            "single_mapping": "综合反映全费用管控能力。费用率抬升 = 管控失效、扩张投入过高侵蚀利润。",
            "is_pending": False,
        },
        {
            "name": "扣非ROE",
            "unit": "%",
            "accounts": ["扣非净利润", "净资产", "期初净资产", "期末净资产"],
            "compute": lambda d: (
                d.get("扣非净利润")
                / (((d.get("期初净资产") or d.get("净资产", 0))
                    + (d.get("期末净资产") or d.get("净资产", 0))) / 2 or 1) * 100
            ) if _needs(d, "扣非净利润") and (d.get("期初净资产") or d.get("净资产")) else None,
            "normal_range": "10%~22%",
            "warning_range": "5%~10%",
            "high_risk_range": "<5%",
            "single_mapping": "净资产回报率。综合杜邦拆解盈利、周转、杠杆三大能力，是股东回报通用核心指标。",
            "is_pending": False,
        },
        # ── 3 pending indicators ──
        {
            "name": "营收同比增速",
            "unit": "%",
            "accounts": ["营业收入", "营业收入上期", "上期营业收入"],
            "compute": lambda d: (
                _compute_yoy(d, "营业收入", "营业收入上期", "上期营业收入")
            ),
            "normal_range": "N/A",
            "warning_range": "N/A",
            "high_risk_range": "N/A",
            "single_mapping": "反映企业业务扩张速度。偏离行业增速代表公司竞争力弱于行业平均水平。",
            "is_pending": True,
        },
        {
            "name": "应收账款同比增速",
            "unit": "%",
            "accounts": ["应收账款", "期初应收账款", "应收账款上期"],
            "compute": lambda d: (
                _compute_yoy(d, "应收账款", "期初应收账款", "应收账款上期")
            ),
            "normal_range": "N/A",
            "warning_range": "N/A",
            "high_risk_range": "N/A",
            "single_mapping": "对比营收增速判断赊销扩张程度。应收增速长期远超营收 = 放宽账期抢订单、收入虚胖。",
            "is_pending": True,
        },
        {
            "name": "存货同比增速",
            "unit": "%",
            "accounts": ["存货", "期初存货", "存货上期"],
            "compute": lambda d: (
                _compute_yoy(d, "存货", "期初存货", "存货上期")
            ),
            "normal_range": "N/A",
            "warning_range": "N/A",
            "high_risk_range": "N/A",
            "single_mapping": "存货增速大幅超过营收 = 原材料囤货或产成品滞销、库存积压。",
            "is_pending": True,
        },
    ]


# ── Main node function ────────────────────────────────────────────────

def calculate_general(
    financial_data: Optional[Dict[str, Optional[float]]] = None,
    notes_data: Optional[Dict[str, Optional[float]]] = None,
) -> Dict[str, Any]:
    """Compute all 11 general financial indicators.

    Args:
        financial_data: Main financial accounts {name: value}.
        notes_data: Notes/additional data (merged with financial_data for lookups).

    Returns:
        Dict with:
            general_indicators: Dict[str, dict] — indicator name → IndicatorResult fields
    """
    data = {}
    if financial_data:
        data.update(financial_data)
    if notes_data:
        data.update(notes_data)

    specs = _build_general_indicator_specs()
    results: Dict[str, dict] = {}

    for spec in specs:
        name = spec["name"]
        is_pending = spec["is_pending"]

        # Check if accounts are available
        missing_accounts = []
        for acct in spec["accounts"]:
            if _get_val(data, acct) is None:
                missing_accounts.append(acct)

        # Compute
        try:
            value = spec["compute"](data)
        except (TypeError, ZeroDivisionError, ValueError):
            value = None

        # For pending indicators: if prior-period data is missing, mark as pending
        if is_pending and value is None:
            risk_level = RiskLevel.PENDING
            threshold_range = "缺少上期数据，无法计算"
        elif value is None:
            risk_level = RiskLevel.PENDING
            threshold_range = f"数据缺失: {', '.join(missing_accounts[:3])}"
        else:
            risk_level, threshold_range = judge_risk_level(
                value,
                spec["normal_range"],
                spec["warning_range"],
                spec["high_risk_range"],
                is_pending=False,
            )

        results[name] = {
            "name": name,
            "value": _fmt_value(value),
            "unit": spec["unit"],
            "formula": spec.get("formula_display", ""),
            "normal_range": spec["normal_range"] if not is_pending else "待补充（需上期数据）",
            "warning_range": spec["warning_range"] if not is_pending else "",
            "high_risk_range": spec["high_risk_range"] if not is_pending else "",
            "risk_level": risk_level,
            "single_mapping": spec["single_mapping"],
            "used_accounts": spec["accounts"],
        }

    return {"general_indicators": results}


# ── Display formulas (for report) ─────────────────────────────────────

_GENERAL_FORMULAS = {
    "销售毛利率": "(营业收入 - 营业成本) / 营业收入 × 100%",
    "扣非销售净利率": "扣非净利润 / 营业收入 × 100%",
    "净利润现金含量": "经营活动现金流净额 / 扣非净利润",
    "资产负债率": "总负债 / 总资产 × 100%",
    "流动比率": "流动资产 / 流动负债",
    "速动比率": "(流动资产 - 存货 - 预付款项) / 流动负债",
    "期间费用率": "(销售+管理+财务+研发费用) / 营业收入 × 100%",
    "扣非ROE": "扣非净利润 / 平均净资产 × 100%",
    "营收同比增速": "(本期营收 - 上期营收) / 上期营收 × 100%",
    "应收账款同比增速": "(期末应收 - 期初应收) / 期初应收 × 100%",
    "存货同比增速": "(期末存货 - 期初存货) / 期初存货 × 100%",
}


def annotate_formulas(indicators: Dict[str, dict]) -> Dict[str, dict]:
    """Annotate indicator results with display formulas."""
    for name, ind in indicators.items():
        if name in _GENERAL_FORMULAS:
            ind["formula"] = _GENERAL_FORMULAS[name]
    return indicators

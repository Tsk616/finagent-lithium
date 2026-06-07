"""
Node 5: filter_key_indicators — 关键指标筛选

Filters key leading indicators and fatal indicators from general_indicators
and sector_indicators for the linkage_analysis node.

Uses:
  1. KB flags (is_key_indicator / is_fatal_indicator) — preferred
  2. Hardcoded fallback table from 06_calculator_sector.md spec

Pure Python — no LLM.
"""

from typing import Dict, List, Optional, Any


# ── Fallback key indicator table ──────────────────────────────────────
# From 06_calculator_sector.md "MVP 初版自动提取清单"
# Used when KB flags are missing (current MVP state).

_KEY_INDICATOR_FALLBACK: Dict[str, Dict[str, List[str]]] = {
    "1.1": {"fatal": ["存货跌价损失率"], "key": ["合同负债同比变动率"]},
    "1.2": {"fatal": ["现金周转周期（CCC）", "现金周转周期"], "key": ["前五客户营收占比"]},
    "2.1": {"fatal": ["应收-营收增速差", "应收 - 营收增速差"], "key": ["研发费用率"]},
    "2.2": {"fatal": ["固定资产周转率"], "key": ["研发费用率"]},
    "2.3": {"fatal": ["存货周转天数", "存货周转率"], "key": ["六氟磷酸锂成本占比"]},
    "2.4": {"fatal": ["固定资产成新率"], "key": ["制造费用占成本比"]},
    "2.5": {"fatal": ["加工费毛利率"], "key": ["金属原料成本占比"]},
    "2.6": {"fatal": [], "key": []},
    "3.1": {"fatal": ["固定资产周转率"], "key": ["存货产成品占比"]},
    "3.2": {"fatal": ["合同负债/近12月营收"], "key": ["海外收入占比"]},
    "3.3": {"fatal": ["存货中在产品占比"], "key": ["研发费用率"]},
    "3.4": {"fatal": ["现金周转周期（CCC）", "现金周转周期"], "key": ["应付账款周转天数"]},
    "3.5": {"fatal": ["固定资产周转率"], "key": ["设备折旧占成本比"]},
    "4.1": {"fatal": ["净营业周期"], "key": ["经营现金流净额/营业收入"]},
    "4.2": {"fatal": ["废旧存货周转天数"], "key": ["梯次利用毛利率"]},
    "4.3": {"fatal": ["存货周转率"], "key": ["净营业周期"]},
    "5.1": {"fatal": ["合同负债/近12月营收"], "key": ["发出商品占比"]},
    "5.2": {"fatal": ["服务收入占总营收比重"], "key": ["合同负债/近12月营收"]},
    "5.3": {"fatal": ["长期服务合约余额/近12月营收"], "key": ["核心技术人员流失率"]},
    "6.1": {"fatal": [], "key": []},  # Integrated: use sub-sector rules
}


# ── Main node function ────────────────────────────────────────────────

def filter_key_indicators(
    general_indicators: Optional[Dict[str, dict]] = None,
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, dict]]]] = None,
    _sector_code: Optional[str] = None,
    sub_sectors: Optional[List[str]] = None,
    kb: Any = None,
) -> Dict[str, Any]:
    """Filter key indicators from general and sector indicator results.

    Args:
        general_indicators: From calculate_general node.
        sector_indicators: From calculate_sector node.
        _sector_code: Sector code (e.g. "1.1", "6.1").
        sub_sectors: Sub-sector codes for integrated enterprises.
        kb: LithiumKnowledgeBase instance.

    Returns:
        Dict with key_indicators_for_linkage.
    """
    general_indicators = general_indicators or {}
    sector_indicators = sector_indicators or {}

    key_indicators: Dict[str, dict] = {}

    # Determine which sector code(s) to use
    if _sector_code == "6.1" and sub_sectors:
        # Integrated: collect key indicators from all sub-sectors
        for sub_code in sub_sectors:
            _collect_for_sector(sub_code, general_indicators, sector_indicators,
                                key_indicators, kb)
    elif _sector_code:
        _collect_for_sector(_sector_code, general_indicators, sector_indicators,
                            key_indicators, kb)
    else:
        # No sector info — collect everything flagged as key/fatal
        _collect_all_key_indicators(general_indicators, sector_indicators,
                                    key_indicators)

    return {"key_indicators_for_linkage": key_indicators}


def _collect_for_sector(
    sector_code: str,
    general_indicators: Dict[str, dict],
    sector_indicators: Dict[str, Dict[str, Dict[str, dict]]],
    result: Dict[str, dict],
    kb: Any,
) -> None:
    """Collect key/fatal indicators for a single sector code."""

    # Try KB first
    key_names: List[str] = []
    fatal_names: List[str] = []

    if kb and hasattr(kb, "get_sector"):
        sector = kb.get_sector(sector_code)
        if sector:
            for cat_inds in sector.get("indicators", {}).values():
                for ind in cat_inds:
                    name = ind.get("name", "")
                    if ind.get("is_fatal_indicator"):
                        fatal_names.append(name)
                    if ind.get("is_key_indicator"):
                        key_names.append(name)

    # Fallback to hardcoded table if KB flags are all missing
    if not key_names and not fatal_names:
        fallback = _KEY_INDICATOR_FALLBACK.get(sector_code, {})
        fatal_names = fallback.get("fatal", [])
        key_names = fallback.get("key", [])

    target_names = set(fatal_names + key_names)

    # Collect from general_indicators
    for name, ind in general_indicators.items():
        if _name_matches(name, target_names):
            result[name] = dict(ind)

    # Collect from sector_indicators
    for sector_label, categories in sector_indicators.items():
        for category, indicators in categories.items():
            for name, ind in indicators.items():
                if _name_matches(name, target_names):
                    result[name] = dict(ind)


def _collect_all_key_indicators(
    general_indicators: Dict[str, dict],
    sector_indicators: Dict[str, Dict[str, Dict[str, dict]]],
    result: Dict[str, dict],
) -> None:
    """Collect all indicators flagged as key/fatal across all sectors."""
    for name, ind in general_indicators.items():
        if ind.get("is_key_indicator") or ind.get("is_fatal_indicator"):
            result[name] = dict(ind)
    for sector_label, categories in sector_indicators.items():
        for category, indicators in categories.items():
            for name, ind in indicators.items():
                if ind.get("is_key_indicator") or ind.get("is_fatal_indicator"):
                    result[name] = dict(ind)


def _name_matches(indicator_name: str, target_names: set) -> bool:
    """Check if indicator_name matches any target (supports partial match)."""
    for target in target_names:
        if target in indicator_name or indicator_name in target:
            return True
    return False

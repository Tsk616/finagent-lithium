"""
Node 7: anomaly_scan — 异常扫描

Python rule engine. Evaluates boolean rules against computed indicators
to detect accounting/financial anomalies.

No LLM — all rules are deterministic boolean expressions.
"""

import re
from typing import Dict, List, Optional, Any


# ── Built-in general anomaly rules ────────────────────────────────────
# These apply to ALL sectors. KB sector-specific rules are loaded dynamically.

_GENERAL_RULES: List[Dict[str, Any]] = [
    {
        "id": "general_1",
        "type": "纸面利润",
        "description_template": "账面净利润为正({net_profit})，但经营现金流为负({ocf})",
        "check": lambda inds: (
            _gv(inds, "净利润") is not None
            and _gv(inds, "净利润") > 0
            and _gv(inds, "经营活动现金流净额") is not None
            and _gv(inds, "经营活动现金流净额") < 0
        ),
        "risk": "利润全是纸面数字，没有真金白银进账",
        "suggested_accounts": ["净利润", "经营活动现金流净额", "应收账款"],
        "severity": 4,
    },
    {
        "id": "general_2",
        "type": "收入虚胖",
        "description_template": "应收增速({ar_yoy}%)远超营收增速({rev_yoy}%)，差值超过20个百分点",
        "check": lambda inds: (
            _gv(inds, "应收账款同比增速") is not None
            and _gv(inds, "营收同比增速") is not None
            and _gv(inds, "应收账款同比增速") > _gv(inds, "营收同比增速") + 20
        ),
        "risk": "靠放宽账期抢订单，收入虚胖，坏账风险高",
        "suggested_accounts": ["应收账款", "营业收入", "信用减值损失"],
        "severity": 3,
    },
    {
        "id": "general_3",
        "type": "流动性陷阱",
        "check": lambda inds: (
            _gv(inds, "流动比率") is not None
            and _gv(inds, "流动比率") < 1.0
            and _gv(inds, "货币资金") is not None
            and _gv(inds, "流动资产") is not None
            and _gv(inds, "流动资产") > 0
            and _gv(inds, "货币资金") / _gv(inds, "流动资产") > 0.5
        ),
        "description_template": "流动比率({cr})<1，但货币资金占流动资产比({cash_ratio}%)异常高",
        "risk": "货币资金可能受限（如存单质押、冻结），实际偿债能力更差",
        "suggested_accounts": ["货币资金", "流动资产", "流动负债", "受限资金"],
        "severity": 4,
    },
    {
        "id": "general_4",
        "type": "存货藏雷",
        "check": lambda inds: (
            _gv(inds, "存货") is not None
            and _gv(inds, "营业收入") is not None
            and _gv(inds, "营业收入") > 0
            and _gv(inds, "存货") > _gv(inds, "营业收入") * 0.3
            and (
                _gv(inds, "存货跌价损失") is None
                or _gv(inds, "存货跌价损失") < _gv(inds, "存货") * 0.02
            )
        ),
        "description_template": "存货({inv})占营收({rev})比超30%，但跌价损失({imp})极低",
        "risk": "隐藏库存贬值损失，虚增利润",
        "suggested_accounts": ["存货", "存货跌价损失", "营业收入"],
        "severity": 5,
    },
    {
        "id": "general_5",
        "type": "费用操纵",
        "check": lambda inds: (
            _gv(inds, "营收同比增速") is not None
            and _gv(inds, "营收同比增速") > 20
            and _gv(inds, "期间费用率") is not None
            and _gv(inds, "期间费用率") < 5
            and _gv(inds, "销售毛利率") is not None
            and _gv(inds, "扣非销售净利率") is not None
            and _gv(inds, "扣非销售净利率") > _gv(inds, "销售毛利率")
        ),
        "description_template": "营收高增({rev_yoy}%)但费用率极低({exp_rate}%)，扣非净利率超过毛利率",
        "risk": "可能通过费用资本化或成本跨期调节来美化利润",
        "suggested_accounts": ["研发费用", "销售费用", "管理费用", "财务费用", "在建工程"],
        "severity": 3,
    },
]


# ── Helper: get indicator value ───────────────────────────────────────

def _gv(indicators: Dict[str, dict], name: str) -> Optional[float]:
    """Get the value of an indicator by name (fuzzy match)."""
    # Exact match
    if name in indicators:
        val = indicators[name].get("value")
        if val is not None:
            return val
    # Fuzzy: check if name is a substring of any key, or vice versa
    for k, v in indicators.items():
        if name in k or k in name:
            val = v.get("value")
            if val is not None:
                return val
    return None


def _normalize_macro_context(macro: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize macro context from Wind or DeepSeek format into anomaly-rule format.

    Target format: {"trend": "上涨"|"下跌"|"震荡", "change_3m_pct": float, "current": float}
    """
    if not macro:
        return {}

    # Wind format already has the right keys
    if "trend" in macro and macro["trend"] in ("上涨", "下跌", "震荡"):
        return macro

    # Nested: {"lithium_trend": {"trend": ..., "change_3m_pct": ..., "current": ...}}
    lt = macro.get("lithium_trend")
    if isinstance(lt, dict) and "trend" in lt:
        return lt

    # DeepSeek text format — extract from Chinese text
    result: Dict[str, Any] = {"trend": "震荡", "change_3m_pct": 0.0, "current": 0.0}

    trend_text = ""
    for key in ("lithium_price_trend", "lithium_trend", "supply_demand"):
        val = macro.get(key)
        if isinstance(val, str) and val != "暂无可靠数据":
            trend_text += " " + val

    if not trend_text:
        return result

    if any(kw in trend_text for kw in ("下跌", "下行", "走低", "下滑", "回落", "降价", "跌")):
        result["trend"] = "下跌"
    elif any(kw in trend_text for kw in ("上涨", "上行", "走高", "上升", "涨价", "反弹")):
        result["trend"] = "上涨"

    pct_match = re.search(r'[跌降涨升幅变动]+[约近]?\s*(\d+(?:\.\d+)?)\s*[%％]', trend_text)
    if pct_match:
        pct = float(pct_match.group(1))
        if result["trend"] == "下跌":
            pct = -pct
        result["change_3m_pct"] = pct

    price_match = re.search(r'(\d+(?:\.\d+)?)\s*万[元/]', trend_text)
    if price_match:
        result["current"] = float(price_match.group(1)) * 10000
    else:
        price_match2 = re.search(r'(\d{4,})\s*元/吨', trend_text)
        if price_match2:
            result["current"] = float(price_match2.group(1))

    return result


def _fmt_val(indicators: Dict[str, dict], name: str) -> str:
    """Format indicator value for description_template."""
    val = _gv(indicators, name)
    if val is None:
        return "?"
    if abs(val) >= 1e8:
        return f"{val/1e8:.1f}亿"
    return f"{val:.2f}"


# ── Main node function ────────────────────────────────────────────────

def anomaly_scan(
    general_indicators: Optional[Dict[str, dict]] = None,
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, dict]]]] = None,
    financial_data: Optional[Dict[str, Optional[float]]] = None,
    _sector_code: Optional[str] = None,
    sub_sectors: Optional[List[str]] = None,
    kb: Any = None,
    macro_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Scan for financial anomalies using rule engine.

    Args:
        general_indicators: From calculate_general.
        sector_indicators: From calculate_sector.
        financial_data: Raw financial data (for direct account access).
        _sector_code: Current sector code.
        sub_sectors: Sub-sector codes for integrated.
        kb: LithiumKnowledgeBase instance.
        macro_context: Optional macro data for external anomaly rules.

    Returns:
        Dict with:
            anomaly_signals: List[AnomalySignal]
            skipped_external_rules: int — count of rules skipped (requires external data)
    """
    general_indicators = general_indicators or {}
    sector_indicators = sector_indicators or {}
    financial_data = financial_data or {}

    # Merge all indicators into one flat lookup dict
    all_indicators: Dict[str, dict] = {}
    all_indicators.update(general_indicators)

    # Also add raw financial data as pseudo-indicators (for direct account access)
    for k, v in financial_data.items():
        if v is not None:
            all_indicators[k] = {"value": v, "risk_level": "", "unit": ""}

    # Add sector indicators
    for sector_label, categories in sector_indicators.items():
        for category, indicators in categories.items():
            for name, ind in indicators.items():
                if name not in all_indicators:
                    all_indicators[name] = ind

    anomaly_signals: List[Dict] = []
    skipped_external = 0

    # ── Run general rules ──
    for rule in _GENERAL_RULES:
        try:
            if rule["check"](all_indicators):
                signal = _build_signal(rule, all_indicators)
                anomaly_signals.append(signal)
        except Exception:
            pass  # Rule evaluation failed — skip gracefully

    # ── Run KB sector-specific rules ──
    if kb and hasattr(kb, "get_anomaly_rules"):
        codes_to_check = []
        if _sector_code:
            codes_to_check.append(_sector_code)
        if sub_sectors:
            codes_to_check.extend(sub_sectors)

        for code in set(codes_to_check):
            for rule in kb.get_anomaly_rules(code, include_external=True):
                if rule.get("requires_external", False):
                    normalized = _normalize_macro_context(macro_context)
                    if normalized and normalized.get("trend"):
                        try:
                            if _evaluate_external_rule(rule, all_indicators, normalized):
                                signal = _build_kb_signal(rule, all_indicators)
                                signal["_source"] = "external_macro"
                                anomaly_signals.append(signal)
                        except Exception:
                            pass
                    else:
                        skipped_external += 1
                    continue

                # Try to compile and evaluate the raw rule text
                try:
                    if _evaluate_kb_rule(rule, all_indicators):
                        signal = _build_kb_signal(rule, all_indicators)
                        anomaly_signals.append(signal)
                except Exception:
                    pass

    # Sort by severity descending
    anomaly_signals.sort(key=lambda s: s.get("severity", 0), reverse=True)

    return {
        "anomaly_signals": anomaly_signals,
        "skipped_external_rules": skipped_external,
    }


def _build_signal(rule: Dict, indicators: Dict[str, dict]) -> Dict:
    """Build an AnomalySignal from a matched general rule."""
    # Fill in description template
    desc = rule.get("description_template", rule.get("risk", ""))
    format_args = {
        "net_profit": _fmt_val(indicators, "净利润"),
        "ocf": _fmt_val(indicators, "经营活动现金流净额"),
        "ar_yoy": _fmt_val(indicators, "应收账款同比增速"),
        "rev_yoy": _fmt_val(indicators, "营收同比增速"),
        "cr": _fmt_val(indicators, "流动比率"),
        "cash_ratio": _fmt_val(indicators, "货币资金"),
        "inv": _fmt_val(indicators, "存货"),
        "rev": _fmt_val(indicators, "营业收入"),
        "imp": _fmt_val(indicators, "存货跌价损失"),
        "exp_rate": _fmt_val(indicators, "期间费用率"),
    }

    # Try simple template substitution
    try:
        description = desc.format(**format_args)
    except (KeyError, ValueError):
        description = desc

    return {
        "rule_id": rule["id"],
        "type": rule.get("type", "异常信号"),
        "description": description,
        "potential_risk": rule["risk"],
        "suggested_accounts": rule.get("suggested_accounts", []),
        "severity": rule["severity"],
    }


def _evaluate_kb_rule(rule: Dict, indicators: Dict[str, dict]) -> bool:
    """Try to evaluate a KB anomaly rule. Returns True if triggered.

    KB rules have a 'raw' field with descriptive text like:
    "市场锂价大幅下跌，但企业存货跌价损失率极低"
    We try to parse this into a checkable condition using indicator values.
    """
    raw = rule.get("raw", "")
    description = rule.get("description", "")
    risk = rule.get("risk", "")
    combined = f"{description} {risk} {raw}".lower()

    # Pattern-based matching for common KB anomaly patterns
    # Pattern: "存货跌价损失率极低" → check if 存货跌价损失率 is low
    if "存货跌价损失" in combined and ("极低" in combined or "很低" in combined or "未计提" in combined or "少计提" in combined):
        val = _gv(indicators, "存货跌价损失率")
        inv_val = _gv(indicators, "存货跌价损失")
        inventory = _gv(indicators, "存货")
        if inv_val is not None and inventory is not None and inventory > 0:
            rate = inv_val / inventory * 100
            if rate < 1.0:
                return True
        if val is not None and val < 1.0:
            return True

    # Pattern: "合同负债高增 + 存货周转恶化"
    if "合同负债" in combined and "存货周转" in combined and ("恶化" in combined or "下降" in combined):
        contract_growth = _gv(indicators, "合同负债同比变动率")
        inv_turnover = _gv(indicators, "存货周转率")
        if contract_growth is not None and contract_growth > 50:
            if inv_turnover is not None and inv_turnover < 3:
                return True

    # Pattern: "营收毛利双下滑 + 固定资产扩张"
    if "营收" in combined and "毛利" in combined and "双下滑" in combined:
        rev_growth = _gv(indicators, "营收同比增速")
        margin = _gv(indicators, "销售毛利率")
        if rev_growth is not None and rev_growth < 0 and margin is not None:
            # Check if fixed assets or construction is growing
            cip_growth = _gv(indicators, "在建工程同比增速")
            if cip_growth is not None and cip_growth > 30:
                return True

    # Pattern: "单位生产成本逆势上涨"
    if "单位生产" in combined and "成本" in combined and ("上涨" in combined or "上升" in combined):
        cost_change = _gv(indicators, "单位锂盐成本变动率")
        if cost_change is not None and cost_change > 10:
            return True

    return False


def _evaluate_external_rule(
    rule: Dict,
    indicators: Dict[str, dict],
    macro: Dict[str, Any],
) -> bool:
    """Evaluate an external-data-dependent anomaly rule using macro context."""
    trend = macro.get("trend", "")
    change_3m_pct = macro.get("change_3m_pct", 0) or 0

    check = rule.get("check", "")
    if check:
        try:
            ctx: Dict[str, Any] = {}
            for name, ind in indicators.items():
                val = ind.get("value") if isinstance(ind, dict) else ind
                if val is not None:
                    safe_name = name.replace(" ", "_").replace("(", "").replace(")", "")
                    ctx[safe_name] = float(val)

            ctx["external_lithium_price_drop"] = (
                abs(change_3m_pct) if trend == "下跌" else 0.0
            )
            ctx["external_lithium_price_rise"] = (
                change_3m_pct if trend == "上涨" else 0.0
            )
            ctx["lithium_price_change_3m"] = change_3m_pct
            ctx["lithium_price_current"] = macro.get("current", 0) or 0
            ctx["lithium_trend_up"] = 1 if trend == "上涨" else 0
            ctx["lithium_trend_down"] = 1 if trend == "下跌" else 0

            # Map English variable names used in KB check expressions
            inv = _gv(indicators, "存货")
            inv_loss = _gv(indicators, "存货跌价损失")
            if inv and inv > 0 and inv_loss is not None:
                ctx["inventory_impairment_rate"] = inv_loss / inv * 100
            else:
                ctx["inventory_impairment_rate"] = 0.0
            ctx["contract_liability_growth"] = _gv(indicators, "合同负债同比增速") or 0.0
            ctx["inventory_turnover"] = _gv(indicators, "存货周转率") or 0.0

            expr = check.replace("AND", " and ").replace("OR", " or ")
            return bool(eval(expr, {"__builtins__": {}}, ctx))
        except Exception:
            return False

    # No check expression — pattern-match on description/raw text
    desc = f"{rule.get('description', '')} {rule.get('raw', '')}".lower()

    # "锂价下跌 + 存货跌价损失低"
    if ("锂价" in desc or "金属价格" in desc) and ("下跌" in desc or "下行" in desc):
        if trend != "下跌" and change_3m_pct > -10:
            return False
        inv = _gv(indicators, "存货")
        inv_loss = _gv(indicators, "存货跌价损失")
        if inv and inv > 0 and inv_loss is not None:
            if inv_loss / inv * 100 < 2.0:
                return True

    # "钴镍/金属价格上涨 + 毛利率不升反降"
    if ("价格" in desc) and ("上涨" in desc or "上升" in desc) and "毛利" in desc:
        if trend == "上涨" or change_3m_pct > 10:
            margin = _gv(indicators, "销售毛利率")
            if margin is not None and margin < 15:
                return True

    # "行业下滑 + 扩张在建工程"
    if "行业" in desc and ("下滑" in desc or "下行" in desc or "装机量" in desc):
        if trend == "下跌" or change_3m_pct < -5:
            cip = _gv(indicators, "在建工程同比增速")
            if cip is not None and cip > 30:
                return True

    return False


def _build_kb_signal(rule: Dict, indicators: Dict[str, dict]) -> Dict:
    """Build an AnomalySignal from a matched KB rule."""
    return {
        "rule_id": rule.get("id", f"kb_{rule.get('description', 'unknown')[:20]}"),
        "type": "知识库规则",
        "description": rule.get("description", ""),
        "potential_risk": rule.get("risk", ""),
        "suggested_accounts": [],
        "severity": 4,  # Default severity for KB rules
    }

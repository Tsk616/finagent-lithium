"""
Node 4: calculate_sector — 赛道专属指标计算

Computes sector-specific indicators from the knowledge base.
For integrated enterprises (6.1), loops over all sub_sectors.

Key logic:
  1. Load sector indicators from KB by sector code
  2. Parse formulas, resolve account values from financial_data/notes_data
  3. Evaluate arithmetic expressions (pure Python, no LLM)
  4. Apply threshold judgment per indicator
  5. Group results by indicator category
"""

import re
from typing import Dict, List, Optional, Any, Tuple

# Reuse risk level and threshold parsing from calculate_general
from nodes.calculate_general import (
    RiskLevel,
    judge_risk_level,
    _fmt_value,
    _parse_threshold,
)


# ── Account value resolver ────────────────────────────────────────────

def _resolve_account_value(
    account_name: str,
    data: Dict[str, Optional[float]],
) -> Optional[float]:
    """Resolve an account name (as it appears in a formula) to a numeric value.

    Resolution strategies (tried in order):
      1. Exact match in data
      2. "平均X" → average of (期初X, 期末X) or fallback to X
      3. Substring/fuzzy match
    """
    if not account_name:
        return None

    name = account_name.strip()

    # Strategy 1: Direct lookup
    if name in data and data[name] is not None:
        return data[name]

    # Strategy 2: "平均X" decomposition — try to compute average
    if name.startswith("平均"):
        base = name[2:]
        begin_val = data.get(f"期初{base}")
        end_val = data.get(f"期末{base}")
        if begin_val is not None and end_val is not None:
            return (begin_val + end_val) / 2
        if base in data and data[base] is not None:
            return data[base]
        # Don't return None — fall through to Strategy 3

    # Strategy 2b: "平均" embedded in name
    if "平均" in name:
        base = name.replace("平均", "").replace("余额", "").strip()
        begin_val = data.get(f"期初{base}")
        end_val = data.get(f"期末{base}")
        if begin_val is not None and end_val is not None:
            return (begin_val + end_val) / 2
        if base in data and data[base] is not None:
            return data[base]
        # Don't return None — fall through to Strategy 3

    # Strategy 3: Fuzzy match (substring in either direction)
    name_lower = name.lower()
    for key, val in data.items():
        if val is None:
            continue
        key_lower = key.lower()
        if name_lower in key_lower or key_lower in name_lower:
            return val

    # Strategy 4: Try stripping suffixes
    for suffix in ["余额", "净额", "原值", "净值", "合计", "总额", "当期", "本期"]:
        if name.endswith(suffix):
            base = name[:-len(suffix)]
            if base in data and data[base] is not None:
                return data[base]

    return None


# ── Formula evaluator ─────────────────────────────────────────────────

def _evaluate_formula(
    formula: str,
    data: Dict[str, Optional[float]],
) -> Optional[float]:
    """Evaluate a KB formula string using provided financial data.

    Handles: +, -, ×, ÷, *, /, (), %, 100%, numbers, Chinese account names.
    Returns None if any required account is missing or evaluation fails.
    """
    if not formula or not formula.strip():
        return None

    # Normalize unicode minus to hyphen-minus
    expr = formula.replace("−", "-")

    # Split the formula into tokens: account names, numbers, operators
    # Extract Chinese account name tokens (sequences of Chinese chars + common suffixes)
    tokens = _tokenize_formula(expr)

    # Build substitution map: account_name → value
    # Use unique tokens to avoid double-mapping
    unique_tokens: Dict[str, float] = {}
    unresolved: List[str] = []

    for token in tokens:
        if _is_account_token(token) and token not in unique_tokens:
            val = _resolve_account_value(token, data)
            if val is not None:
                unique_tokens[token] = val
            else:
                unresolved.append(token)

    # If any required account is missing, return None
    if unresolved:
        return None

    # Replace ALL occurrences of each account name with a placeholder.
    # Sort by length descending so "期初合同负债" is replaced before "合同负债".
    substitutions: Dict[str, float] = {}
    sorted_tokens = sorted(unique_tokens.items(), key=lambda x: -len(x[0]))
    for i, (token, val) in enumerate(sorted_tokens):
        placeholder = f"__A{i}__"
        expr = expr.replace(token, placeholder)
        substitutions[placeholder] = val

    # Now substitute the placeholders with actual values
    for placeholder, val in substitutions.items():
        expr = expr.replace(placeholder, str(val))

    # Clean up: remove any remaining Chinese text (descriptive notes)
    expr = re.sub(r'[一-鿿]+', '', expr)

    # Handle percentage marks: "×100%" → "*100/100" (the % means divide by 100)
    # But in formulas, "×100%" typically means multiply by 100 for display
    # Actually: "X/Y×100%" means (X/Y)*100 — the % is just a unit label
    expr = expr.replace("%", "")

    # Replace operators
    expr = expr.replace("÷", "/").replace("×", "*")

    # Collapse multiple spaces
    expr = re.sub(r'\s+', ' ', expr).strip()

    # Safety check: expression should only contain digits, operators, parens, dots
    if not re.match(r'^[\d\s+\-*/().]+$', expr):
        return None

    # Handle empty expression
    if not expr.strip():
        return None

    try:
        result = eval(expr, {"__builtins__": {}}, {})
        if isinstance(result, (int, float)) and not _is_invalid_number(result):
            return float(result)
    except (SyntaxError, ZeroDivisionError, ValueError, TypeError):
        pass

    return None


def _is_invalid_number(val: float) -> bool:
    """Check if a float is NaN, Inf, or absurdly large."""
    import math
    return math.isnan(val) or math.isinf(val) or abs(val) > 1e15


def _tokenize_formula(expr: str) -> List[str]:
    """Extract potential account name tokens from a formula string.

    Returns all maximal sequences that could be account names
    (Chinese characters, letters, common separators).
    """
    # Split on operators, numbers, whitespace
    parts = re.split(r'[÷+\-−×*/()（）\d.%=\s;；，,、\n]+', expr)
    tokens = []
    for p in parts:
        p = p.strip()
        if p:
            tokens.append(p)
    return tokens


def _is_account_token(token: str) -> bool:
    """Check if a token looks like an account name (not an operator/descriptor)."""
    # Must have Chinese characters or be a recognizable account indicator
    if re.search(r'[一-鿿]', token):
        return True
    # English account names
    if re.match(r'^[A-Za-z][A-Za-z\s/&]+$', token) and len(token) > 3:
        return True
    return False


# ── Main node function ────────────────────────────────────────────────

def calculate_sector(
    financial_data: Optional[Dict[str, Optional[float]]] = None,
    notes_data: Optional[Dict[str, Optional[float]]] = None,
    sector_level2: Optional[str] = None,
    sub_sectors: Optional[List[str]] = None,
    _sector_code: Optional[str] = None,
    kb: Any = None,
) -> Dict[str, Any]:
    """Compute sector-specific indicators from the knowledge base.

    Args:
        financial_data: Main financial accounts.
        notes_data: Notes/additional data (used for notes-dependent indicators).
        sector_level2: Full sector name (for display).
        sub_sectors: Sub-sector codes for integrated enterprises.
        _sector_code: Sector code from classify_sector (for KB lookup).
        kb: LithiumKnowledgeBase instance.

    Returns:
        Dict with:
            sector_indicators: Dict[str, Dict[str, Dict[str, dict]]]
                Top-level key: sector label (e.g., "3.1 动力电池电芯")
                Second-level: indicator category (e.g., "营运效率类")
                Third-level: indicator name → IndicatorResult fields
    """
    data: Dict[str, Optional[float]] = {}
    if financial_data:
        data.update(financial_data)
    if notes_data:
        data.update(notes_data)

    sector_indicators: Dict[str, Dict[str, Dict[str, dict]]] = {}

    # Determine which sector codes to process
    if _sector_code == "6.1" and sub_sectors:
        codes_to_process = sub_sectors
    elif _sector_code:
        codes_to_process = [_sector_code]
    else:
        # No sector code — return empty
        return {"sector_indicators": sector_indicators}

    for code in codes_to_process:
        sector_label = _get_sector_label(code, kb) or code
        indicators = _compute_sector_indicators(code, data, kb)
        sector_indicators[sector_label] = indicators

    return {"sector_indicators": sector_indicators}


def _get_sector_label(code: str, kb: Any) -> Optional[str]:
    """Get a human-readable label for a sector code."""
    if kb and hasattr(kb, "get_sector"):
        sector = kb.get_sector(code)
        if sector:
            name = sector.get("name", "")
            # Use short form for code: "3.1 动力电池电芯（车用）" → "3.1 动力电池电芯"
            return f"{code} {name}"
    return None


def _compute_sector_indicators(
    sector_code: str,
    data: Dict[str, Optional[float]],
    kb: Any,
) -> Dict[str, Dict[str, dict]]:
    """Compute all indicators for a single sector."""
    results: Dict[str, Dict[str, dict]] = {}

    if not kb or not hasattr(kb, "get_sector"):
        return results

    sector = kb.get_sector(sector_code)
    if not sector:
        return results

    for category, indicator_list in sector.get("indicators", {}).items():
        category_results: Dict[str, dict] = {}
        for ind in indicator_list:
            name = ind.get("name", "")
            formula = ind.get("formula", "")
            unit = ind.get("unit", "")
            normal_raw = ind.get("normal_range", "")
            warning_raw = ind.get("warning_range", "")
            high_risk_raw = ind.get("high_risk_range", "")
            single_mapping = ind.get("single_mapping", "")
            accounts_needed = ind.get("accounts_needed", [])

            # Try to compute the indicator value
            value = None

            # Strategy A: use accounts_needed if populated
            if accounts_needed:
                acct_values = {}
                all_present = True
                for acct in accounts_needed:
                    v = _resolve_account_value(acct, data)
                    if v is None:
                        all_present = False
                        break
                    acct_values[acct] = v
                if all_present:
                    value = _evaluate_formula(formula, data)

            # Strategy B: evaluate formula directly (extracts accounts from formula text)
            if value is None:
                value = _evaluate_formula(formula, data)

            # Apply threshold judgment
            if value is not None:
                risk_level, threshold_range = judge_risk_level(
                    value, normal_raw, warning_raw, high_risk_raw, is_pending=False
                )
            else:
                risk_level = RiskLevel.PENDING
                threshold_range = "数据缺失"

            category_results[name] = {
                "name": name,
                "value": _fmt_value(value),
                "unit": unit,
                "formula": formula,
                "normal_range": normal_raw,
                "warning_range": warning_raw,
                "high_risk_range": high_risk_raw,
                "risk_level": risk_level,
                "single_mapping": single_mapping,
                "used_accounts": accounts_needed if accounts_needed
                                 else _extract_account_names(formula),
                "is_key_indicator": ind.get("is_key_indicator", False),
                "is_fatal_indicator": ind.get("is_fatal_indicator", False),
                "_sector_code": sector_code,
            }

        if category_results:
            results[category] = category_results

    return results


def _extract_account_names(formula: str) -> List[str]:
    """Extract potential account names from formula for display purposes."""
    if not formula:
        return []
    parts = re.split(r'[÷+\-−×*/()（）\d.%=\s;；，,、\n]+', formula)
    names = []
    for p in parts:
        p = p.strip()
        if p and re.search(r'[一-鿿]', p) and len(p) >= 2:
            names.append(p)
    return names


# ── Test data ─────────────────────────────────────────────────────────

def _test_data_sample(company: str) -> Dict[str, Optional[float]]:
    """Return enriched test data including prior-period and average accounts."""
    samples = {
        "宁德时代": {
            # 利润表
            "营业收入": 400000000000.0,
            "营业成本": 300000000000.0,
            "净利润": 44000000000.0,
            "扣非净利润": 40000000000.0,
            "销售费用": 12000000000.0,
            "管理费用": 8000000000.0,
            "财务费用": -2000000000.0,
            "研发费用": 18000000000.0,
            "经营活动现金流净额": 92000000000.0,
            # 资产负债表
            "总资产": 670000000000.0,
            "总负债": 450000000000.0,
            "净资产": 220000000000.0,
            "流动资产": 420000000000.0,
            "流动负债": 300000000000.0,
            "存货": 45000000000.0,
            "应收账款": 65000000000.0,
            "固定资产": 180000000000.0,
            "货币资金": 280000000000.0,
            "合同负债": 22000000000.0,
            "预付款项": 5000000000.0,
            "在建工程": 30000000000.0,
            # 期初/平均数据
            "期初存货": 40000000000.0,
            "期末存货": 45000000000.0,
            "期初应收账款": 55000000000.0,
            "期末应收账款": 65000000000.0,
            "平均存货": 42500000000.0,
            "平均固定资产": 170000000000.0,
            "平均净资产": 200000000000.0,
            "期初净资产": 180000000000.0,
            "期末净资产": 220000000000.0,
            "期初固定资产": 160000000000.0,
            "期末固定资产": 180000000000.0,
            "期初在建工程": 20000000000.0,
            "期末在建工程": 30000000000.0,
            "期初合同负债": 18000000000.0,
            "期末合同负债": 22000000000.0,
            "期初总资产": 600000000000.0,
            "期末总资产": 670000000000.0,
            "期初总负债": 420000000000.0,
            "期末总负债": 450000000000.0,
            "期初流动资产": 380000000000.0,
            "期末流动资产": 420000000000.0,
            "期初流动负债": 280000000000.0,
            "期末流动负债": 300000000000.0,
            # 上期数据
            "营业收入上期": 320000000000.0,
            "应收账款上期": 55000000000.0,
            "存货上期": 40000000000.0,
        },
        "天齐锂业": {
            "营业收入": 40000000000.0,
            "营业成本": 15000000000.0,
            "净利润": 7000000000.0,
            "扣非净利润": 6500000000.0,
            "销售费用": 800000000.0,
            "管理费用": 1200000000.0,
            "财务费用": 500000000.0,
            "研发费用": 300000000.0,
            "经营活动现金流净额": 12000000000.0,
            "总资产": 70000000000.0,
            "总负债": 25000000000.0,
            "净资产": 45000000000.0,
            "流动资产": 30000000000.0,
            "流动负债": 12000000000.0,
            "存货": 5000000000.0,
            "应收账款": 3000000000.0,
            "固定资产": 25000000000.0,
            "货币资金": 8000000000.0,
            "合同负债": 3000000000.0,
            "预付款项": 1000000000.0,
            "在建工程": 5000000000.0,
            "期初存货": 4500000000.0,
            "期末存货": 5000000000.0,
            "平均存货": 4750000000.0,
            "平均固定资产": 24000000000.0,
            "平均净资产": 42000000000.0,
            "期初净资产": 39000000000.0,
            "期末净资产": 45000000000.0,
            "期初固定资产": 23000000000.0,
            "期末固定资产": 25000000000.0,
        },
        "比亚迪": {
            "营业收入": 600000000000.0,
            "营业成本": 480000000000.0,
            "净利润": 30000000000.0,
            "扣非净利润": 25000000000.0,
            "销售费用": 25000000000.0,
            "管理费用": 18000000000.0,
            "财务费用": -1000000000.0,
            "研发费用": 40000000000.0,
            "经营活动现金流净额": 160000000000.0,
            "总资产": 680000000000.0,
            "总负债": 520000000000.0,
            "净资产": 160000000000.0,
            "流动资产": 280000000000.0,
            "流动负债": 350000000000.0,
            "存货": 80000000000.0,
            "应收账款": 50000000000.0,
            "固定资产": 250000000000.0,
            "货币资金": 90000000000.0,
            "合同负债": 35000000000.0,
            "预付款项": 8000000000.0,
            "在建工程": 60000000000.0,
            "期初存货": 70000000000.0,
            "期末存货": 80000000000.0,
            "平均存货": 75000000000.0,
            "平均固定资产": 230000000000.0,
            "平均净资产": 140000000000.0,
            "期初净资产": 120000000000.0,
            "期末净资产": 160000000000.0,
            "期初固定资产": 210000000000.0,
            "期末固定资产": 250000000000.0,
            "期初应收账款": 40000000000.0,
            "期末应收账款": 50000000000.0,
            "期初合同负债": 25000000000.0,
            "期末合同负债": 35000000000.0,
            "期初在建工程": 40000000000.0,
            "期末在建工程": 60000000000.0,
        },
    }
    return samples.get(company, {})

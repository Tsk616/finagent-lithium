"""
Unit tests for calculate_general and calculate_sector nodes.

Tests cover:
  - All 11 general indicators: computed values, threshold judgments
  - 3 pending indicators (prior-period data missing → 待补充)
  - Sector-specific indicators for 1.1, 3.1 (from KB)
  - Integrated enterprise (6.1) multi-sector computation
  - Formula evaluator: edge cases, duplicate tokens, fuzzy matching
  - Boundary value judgment (conservative rule)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.calculate_general import (
    calculate_general,
    judge_risk_level,
    RiskLevel,
    _parse_threshold,
    annotate_formulas,
)
from nodes.calculate_sector import (
    calculate_sector,
    _evaluate_formula,
    _resolve_account_value,
    _test_data_sample as sector_test_data,
)

try:
    from lithium_kb import LithiumKnowledgeBase
    _KB_PATH = Path(__file__).resolve().parent.parent / "lithium_knowledge_base.json"
    KB = LithiumKnowledgeBase(_KB_PATH).load() if _KB_PATH.exists() else None
except Exception:
    KB = None


def ok(label: str): print(f"  [PASS] {label}")
def fail(label: str, detail=""): print(f"  [FAIL] {label}  -- {detail}")
def assert_eq(actual, expected, label: str) -> bool:
    if actual == expected: ok(label); return True
    fail(label, f"expected={expected}, got={actual}"); return False
def assert_approx(actual, expected, label: str, tol=0.1) -> bool:
    if expected is None:
        if actual is None: ok(label); return True
        fail(label, f"expected None, got {actual}"); return False
    if actual is not None and abs(actual - expected) < tol: ok(label); return True
    fail(label, f"expected≈{expected}, got={actual}"); return False
def assert_in(needle, haystack, label: str) -> bool:
    if needle in str(haystack): ok(label); return True
    fail(label, f"'{needle}' not found"); return False


# ═══════════════════════════════════════════════════════════════════════
# Threshold parsing tests
# ═══════════════════════════════════════════════════════════════════════

def test_threshold_parsing():
    print("\n--- Threshold parsing ---")
    t = _parse_threshold(">=1.2")
    assert_eq(t, {"type": "gte", "value": 1.2}, ">=1.2")
    t = _parse_threshold("<4")
    assert_eq(t, {"type": "lt", "value": 4.0}, "<4")
    t = _parse_threshold("5~9")
    assert_eq(t, {"type": "range", "low": 5.0, "high": 9.0}, "5~9")
    t = _parse_threshold("30%~55%")
    assert_eq(t, {"type": "range", "low": 30.0, "high": 55.0}, "30%~55%")
    t = _parse_threshold("0.6~1.2")
    assert_eq(t, {"type": "range", "low": 0.6, "high": 1.2}, "0.6~1.2")
    t = _parse_threshold(">70%")
    assert_eq(t, {"type": "gt", "value": 70.0}, ">70%")
    t = _parse_threshold("＜0.7")
    assert_eq(t, {"type": "lt", "value": 0.7}, "＜0.7 (fullwidth)")
    t = _parse_threshold("N/A")
    assert_eq(t, None, "N/A → None")
    t = _parse_threshold("基准 ±5pct")
    assert_eq(t, None, "benchmark → None")


def test_risk_judgment():
    print("\n--- Risk judgment ---")

    # 流动比率: normal>=1.2, warning=1.0~1.2, high=<1.0
    rl, __ = judge_risk_level(1.4, ">=1.2", "1.0~1.2", "<1.0")
    assert_eq(rl, RiskLevel.NORMAL, "flow 1.4 → normal")
    rl, __ = judge_risk_level(1.2, ">=1.2", "1.0~1.2", "<1.0")
    assert_eq(rl, RiskLevel.NORMAL, "flow 1.2 (boundary) → normal")
    rl, __ = judge_risk_level(1.1, ">=1.2", "1.0~1.2", "<1.0")
    assert_eq(rl, RiskLevel.WARNING, "flow 1.1 → warning")
    rl, __ = judge_risk_level(0.9, ">=1.2", "1.0~1.2", "<1.0")
    assert_eq(rl, RiskLevel.HIGH_RISK, "flow 0.9 → high risk")

    # 资产负债率: normal=30~55, warning=55~70, high=>70
    rl, __ = judge_risk_level(45, "30%~55%", "55%~70%", ">70%")
    assert_eq(rl, RiskLevel.NORMAL, "leverage 45% → normal")
    rl, __ = judge_risk_level(55, "30%~55%", "55%~70%", ">70%")
    assert_eq(rl, RiskLevel.NORMAL, "leverage 55% (boundary) → normal (conservative toward higher risk?)")
    rl, __ = judge_risk_level(67.16, "30%~55%", "55%~70%", ">70%")
    assert_eq(rl, RiskLevel.WARNING, "leverage 67% → warning")
    rl, __ = judge_risk_level(75, "30%~55%", "55%~70%", ">70%")
    assert_eq(rl, RiskLevel.HIGH_RISK, "leverage 75% → high risk")

    # 净利润现金含量: normal>=1.0, warning=0.7~1.0, high=<0.7
    rl, __ = judge_risk_level(2.3, ">=1.0", "0.7~1.0", "<0.7")
    assert_eq(rl, RiskLevel.NORMAL, "cash 2.3 → normal")
    rl, __ = judge_risk_level(0.5, ">=1.0", "0.7~1.0", "<0.7")
    assert_eq(rl, RiskLevel.HIGH_RISK, "cash 0.5 → high risk")

    # Pending indicator
    rl, __ = judge_risk_level(None, "N/A", "N/A", "N/A", is_pending=True)
    assert_eq(rl, RiskLevel.PENDING, "None + pending → PENDING")


# ═══════════════════════════════════════════════════════════════════════
# calculate_general tests
# ═══════════════════════════════════════════════════════════════════════

def test_general_ningde():
    """宁德时代: all 11 indicators computed correctly."""
    print("\n--- General indicators (宁德时代) ---")
    data = sector_test_data("宁德时代")
    result = calculate_general(financial_data=data)
    result = annotate_formulas(result["general_indicators"])

    # 8 computable
    assert_approx(result["销售毛利率"]["value"], 25.0, "Gross margin = 25%")
    assert_approx(result["扣非销售净利率"]["value"], 10.0, "Net margin = 10%")
    assert_approx(result["净利润现金含量"]["value"], 2.3, "Cash ratio = 2.3")
    assert_approx(result["资产负债率"]["value"], 67.16, "Debt ratio ≈ 67.16%")
    assert_approx(result["流动比率"]["value"], 1.4, "Current ratio = 1.4")
    assert_approx(result["速动比率"]["value"], 1.23, "Quick ratio ≈ 1.23")
    assert_approx(result["期间费用率"]["value"], 9.0, "Expense ratio = 9%")
    assert_approx(result["扣非ROE"]["value"], 20.0, "ROE = 20%")

    # 3 pending (have prior-period data)
    assert_approx(result["营收同比增速"]["value"], 25.0, "Revenue YoY = 25%")
    assert_approx(result["应收账款同比增速"]["value"], 18.18, "AR YoY ≈ 18.18%")
    assert_approx(result["存货同比增速"]["value"], 12.5, "Inventory YoY = 12.5%")

    # Risk levels
    assert_eq(result["销售毛利率"]["risk_level"], RiskLevel.NORMAL, "Gross margin: normal")
    assert_eq(result["资产负债率"]["risk_level"], RiskLevel.WARNING, "Debt 67%: warning")
    assert_eq(result["流动比率"]["risk_level"], RiskLevel.NORMAL, "Current ratio: normal")
    assert_eq(result["净利润现金含量"]["risk_level"], RiskLevel.NORMAL, "Cash content: normal")

    # All indicators should have value (none missing for this complete dataset)
    missing = [k for k, v in result.items() if v["value"] is None]
    assert_eq(len(missing), 0, f"No missing indicators (got {missing})")

    print(f"  Computed {len(result)} indicators, all with values")
    for name, ind in sorted(result.items()):
        print(f"    {name}: {ind['value']} {ind['unit']} [{ind['risk_level'].value}]")


def test_general_sparse():
    """Sparse data: 3 pending indicators should be marked as 待补充."""
    print("\n--- General indicators (sparse, no prior data) ---")
    sparse = {
        "营业收入": 10000000000.0,
        "营业成本": 7000000000.0,
        "净利润": 2000000000.0,
    }
    result = calculate_general(financial_data=sparse)
    indicators = result["general_indicators"]

    # Computable with partial data
    assert_approx(indicators["销售毛利率"]["value"], 30.0, "Gross margin (sparse)")

    # Pending should be PENDING (no prior data)
    for name in ["营收同比增速", "应收账款同比增速", "存货同比增速"]:
        ind = indicators.get(name)
        if ind:
            assert_eq(ind["risk_level"], RiskLevel.PENDING, f"{name} → PENDING")
            assert_eq(ind["value"], None, f"{name} value → None")

    # Missing accounts should yield PENDING
    missing_count = sum(1 for ind in indicators.values() if ind["risk_level"] == RiskLevel.PENDING)
    print(f"  PENDING indicators: {missing_count}/11")
    assert missing_count >= 3, f"At least 3 pending, got {missing_count}"


def test_general_edge_cases():
    """Edge cases: zero revenue, negative values, missing fields."""
    print("\n--- General indicators (edge cases) ---")

    # Zero revenue
    result = calculate_general(financial_data={"营业收入": 0.0, "营业成本": 5000000.0})
    assert_eq(result["general_indicators"]["销售毛利率"]["value"], None, "Zero revenue → None margin")

    # Zero denominator
    result = calculate_general(financial_data={"扣非净利润": 1000000.0, "经营活动现金流净额": 2000000.0, "营业收入": 5000000.0})
    assert_approx(result["general_indicators"]["净利润现金含量"]["value"], 2.0, "Cash/Profit with no zero-div")

    # Division by zero handled gracefully
    result = calculate_general(financial_data={"流动资产": 100.0, "流动负债": 0.0})
    assert_eq(result["general_indicators"]["流动比率"]["value"], None, "Divide by zero → None")


# ═══════════════════════════════════════════════════════════════════════
# Formula evaluator tests
# ═══════════════════════════════════════════════════════════════════════

def test_formula_evaluator():
    print("\n--- Formula evaluator ---")
    data = {
        "营业收入": 400e9, "营业成本": 300e9,
        "平均存货": 42.5e9, "平均固定资产": 170e9,
        "期初存货": 40e9, "期末存货": 45e9,
        "存货跌价损失": 0.9e9,
        "期初合同负债": 18e9, "期末合同负债": 22e9,
        "扣非净利润": 40e9, "经营活动现金流净额": 92e9,
        "流动资产": 420e9, "流动负债": 300e9, "存货": 45e9,
        "期初应收账款": 55e9, "期末应收账款": 65e9,
    }

    tests = [
        ("营业成本 ÷ 平均存货", 300e9 / 42.5e9),
        ("营业收入 ÷ 平均固定资产", 400e9 / 170e9),
        ("(期末合同负债 - 期初合同负债)÷ 期初合同负债 ×100%", (22e9 - 18e9) / 18e9 * 100),
        ("当期存货跌价损失 ÷ 平均存货余额 ×100%", 0.9e9 / 42.5e9 * 100),
        ("经营活动现金流净额 ÷ 扣非净利润", 92e9 / 40e9),
        ("(营业收入 - 营业成本)÷ 营业收入 ×100%", 25.0),
        ("(流动资产 - 存货)÷ 流动负债", (420e9 - 45e9) / 300e9),
        ("(期末应收账款 - 期初应收账款)÷ 期初应收账款 ×100%", (65e9 - 55e9) / 55e9 * 100),
    ]

    for formula, expected in tests:
        result = _evaluate_formula(formula, data)
        assert_approx(result, expected, f"Eval: {formula[:50]}", tol=0.1)

    # Missing account → None
    result = _evaluate_formula("不存在的科目 ÷ 营业收入", data)
    assert_eq(result, None, "Missing account → None")


def test_account_resolution():
    print("\n--- Account resolution ---")
    data = {
        "期初存货": 100.0, "期末存货": 200.0,
        "存货": 150.0, "平均存货": 150.0,
        "营业收入": 1000.0,
    }

    assert_approx(_resolve_account_value("期初存货", data), 100.0, "Direct: 期初存货")
    assert_approx(_resolve_account_value("平均存货", data), 150.0, "Direct: 平均存货")
    assert_approx(_resolve_account_value("平均存货余额", data), 150.0, "Embed avg: 平均存货余额")
    assert_approx(_resolve_account_value("平均固定资产", data), None, "Missing: 平均固定资产 → None")
    # English→Chinese requires AccountResolver; _resolve_account_value only does
    # substring matching within Chinese/English respectively.
    assert_approx(_resolve_account_value("营业收入", data), 1000.0, "Chinese direct: 营业收入")
    assert_approx(_resolve_account_value("Revenue", data), None, "English alone → None (use AccountResolver instead)")


# ═══════════════════════════════════════════════════════════════════════
# calculate_sector tests
# ═══════════════════════════════════════════════════════════════════════

def test_sector_1_1():
    """Sector 1.1 (锂资源开采): compute from KB."""
    print("\n--- Sector indicators (1.1 锂资源开采) ---")
    if KB is None:
        print("  [SKIP] KB not available")
        return

    data = sector_test_data("天齐锂业")
    result = calculate_sector(
        financial_data=data,
        _sector_code="1.1",
        kb=KB,
    )
    indicators = result["sector_indicators"]

    # Should have a key for sector 1.1
    sector_keys = list(indicators.keys())
    assert len(sector_keys) > 0, "Has sector indicators"
    sector_data = indicators[sector_keys[0]]
    print(f"  Sector entry: {sector_keys[0]}")

    # Count indicators by category
    total = 0
    computed = 0
    for category, inds in sector_data.items():
        for name, ind in inds.items():
            total += 1
            if ind["value"] is not None:
                computed += 1
            status = "✓" if ind["value"] is not None else "✗"
            print(f"    [{status}] {category}/{name}: {ind['value']} {ind['unit']} [{ind['risk_level'].value}]")

    print(f"  Total: {total} indicators, {computed} computed, {total - computed} pending")
    assert total > 0, "Has indicators for sector 1.1"
    assert computed >= 1, "At least 1 indicator computed"


def test_sector_3_1():
    """Sector 3.1 (动力电池): compute from KB."""
    print("\n--- Sector indicators (3.1 动力电池) ---")
    if KB is None:
        print("  [SKIP] KB not available")
        return

    data = sector_test_data("宁德时代")
    result = calculate_sector(
        financial_data=data,
        _sector_code="3.1",
        kb=KB,
    )
    indicators = result["sector_indicators"]

    sector_keys = list(indicators.keys())
    assert len(sector_keys) > 0, "Has sector indicators for 3.1"
    sector_data = indicators[sector_keys[0]]

    total = 0
    computed = 0
    for category, inds in sector_data.items():
        for name, ind in inds.items():
            total += 1
            if ind["value"] is not None:
                computed += 1
            status = "✓" if ind["value"] is not None else "✗"
            print(f"    [{status}] {category}/{name}: {ind['value']} {ind['unit']}")

    print(f"  Total: {total}, computed: {computed}")
    assert total > 0, "Has indicators for sector 3.1"


def test_integrated_enterprise():
    """Integrated enterprise (6.1 → sub_sectors)."""
    print("\n--- Integrated enterprise (比亚迪, subs: 3.1, 3.2, 3.3) ---")
    if KB is None:
        print("  [SKIP] KB not available")
        return

    data = sector_test_data("比亚迪")
    result = calculate_sector(
        financial_data=data,
        sub_sectors=["3.1", "3.2", "3.3"],
        _sector_code="6.1",
        kb=KB,
    )
    indicators = result["sector_indicators"]

    sector_keys = list(indicators.keys())
    print(f"  Sub-sector entries: {sector_keys}")
    assert len(sector_keys) >= 1, "At least 1 sub-sector computed"

    total_inds = 0
    for key, sector_data in indicators.items():
        count = sum(len(inds) for inds in sector_data.values())
        total_inds += count
        print(f"    {key}: {count} indicators")
    print(f"  Total across all sub-sectors: {total_inds}")
    assert total_inds > 0, "Has indicators for integrated enterprise"


# ═══════════════════════════════════════════════════════════════════════
# Node chaining: classify → validate → calculate_general → calculate_sector
# ═══════════════════════════════════════════════════════════════════════

def test_full_pipeline_ningde():
    """Full pipeline for 宁德时代."""
    print("\n" + "=" * 60)
    print("Full Pipeline: 宁德时代")
    print("=" * 60)

    from nodes.classify_sector import classify_sector
    from nodes.validate_data import validate_data

    data = sector_test_data("宁德时代")

    # Node 1
    sector = classify_sector(
        company_name="宁德时代", stock_code="300750",
        primary_business=["动力电池系统", "储能系统", "电池材料"], kb=KB)
    print(f"  [1-classify] {sector['sector_level1']} / {sector.get('_sector_code')}")

    # Node 2
    valid = validate_data(financial_data=data, sector_level2=sector.get("sector_level2"), kb=KB)
    print(f"  [2-validate] valid={valid['validated']}, completeness={valid['data_completeness']:.1%}")

    # Node 3
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    computed_gen = sum(1 for ind in general.values() if ind["value"] is not None)
    print(f"  [3-calc_general] {computed_gen}/11 indicators computed")

    # Node 4
    sector_inds = calculate_sector(
        financial_data=data,
        _sector_code=sector.get("_sector_code"),
        sub_sectors=sector.get("sub_sectors"),
        kb=KB,
    )
    total_sec = sum(
        len(inds) for sec_data in sector_inds["sector_indicators"].values()
        for inds in sec_data.values()
    )
    computed_sec = sum(
        1 for sec_data in sector_inds["sector_indicators"].values()
        for inds in sec_data.values()
        for ind in inds.values() if ind["value"] is not None
    )
    print(f"  [4-calc_sector] {computed_sec}/{total_sec} sector indicators computed")
    print(f"  Pipeline: [OK]")


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("FinAgent-Lithium Calculator Node Tests")
    print(f"KB loaded: {KB is not None}")

    # Threshold & risk tests
    print("\n" + "=" * 60)
    print("  Threshold & Risk Level")
    print("=" * 60)
    test_threshold_parsing()
    test_risk_judgment()

    # Formula evaluator
    print("\n" + "=" * 60)
    print("  Formula Evaluator")
    print("=" * 60)
    test_formula_evaluator()
    test_account_resolution()

    # General indicators
    print("\n" + "=" * 60)
    print("  calculate_general")
    print("=" * 60)
    test_general_ningde()
    test_general_sparse()
    test_general_edge_cases()

    # Sector indicators
    print("\n" + "=" * 60)
    print("  calculate_sector")
    print("=" * 60)
    test_sector_1_1()
    test_sector_3_1()
    test_integrated_enterprise()

    # Full pipeline
    test_full_pipeline_ningde()

    print("\n" + "=" * 60)
    print("  All tests complete.")
    print("=" * 60)

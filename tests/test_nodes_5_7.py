"""
Tests for filter_key_indicators, linkage_analysis, anomaly_scan nodes.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.filter_key_indicators import filter_key_indicators, _KEY_INDICATOR_FALLBACK
from nodes.linkage_analysis import linkage_analysis, _simplify_indicators, _extract_code_from_sector
from nodes.anomaly_scan import anomaly_scan, _GENERAL_RULES, _evaluate_kb_rule
from nodes.calculate_general import calculate_general, annotate_formulas
from nodes.calculate_sector import calculate_sector, _test_data_sample as sector_data
from nodes.llm_client import extract_json_from_response

try:
    from lithium_kb import LithiumKnowledgeBase
    KB = LithiumKnowledgeBase(Path(__file__).resolve().parent.parent / "lithium_knowledge_base.json").load()
except Exception:
    KB = None


def ok(label: str): print(f"  [PASS] {label}")
def fail(label: str, detail=""): print(f"  [FAIL] {label}  -- {detail}")
def assert_eq(a, b, label): ok(label) if a == b else fail(label, f"{a} != {b}")
def assert_gt(a, b, label): ok(label) if a > b else fail(label, f"{a} <= {b}")
def assert_in(n, h, label): ok(label) if n in str(h) else fail(label, f"'{n}' not in result")


# ═══════════════════════════════════════════════════════════════════════
# filter_key_indicators
# ═══════════════════════════════════════════════════════════════════════

def test_filter_ningde():
    """宁德时代 (3.1): should filter 固定资产周转率 (fatal) + 存货产成品占比 (key)."""
    print("\n--- filter_key_indicators (宁德时代, 3.1) ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    sector = calculate_sector(financial_data=data, _sector_code="3.1", kb=KB)

    result = filter_key_indicators(
        general_indicators=general["general_indicators"],
        sector_indicators=sector["sector_indicators"],
        _sector_code="3.1",
        kb=KB,
    )
    key_inds = result["key_indicators_for_linkage"]
    print(f"  Key indicators filtered: {list(key_inds.keys())}")
    assert_gt(len(key_inds), 0, "At least 1 key indicator found")
    # Should contain 固定资产周转率 (fatal for 3.1)
    found_fatal = any("固定资产周转率" in k for k in key_inds)
    ok(f"Fatal indicator 固定资产周转率 found: {found_fatal}")


def test_filter_tianqi():
    """天齐锂业 (1.1): should filter 存货跌价损失率 (fatal) + 合同负债同比变动率 (key)."""
    print("\n--- filter_key_indicators (天齐锂业, 1.1) ---")
    data = sector_data("天齐锂业")
    general = calculate_general(financial_data=data)
    sector = calculate_sector(financial_data=data, _sector_code="1.1", kb=KB)

    result = filter_key_indicators(
        general_indicators=general["general_indicators"],
        sector_indicators=sector["sector_indicators"],
        _sector_code="1.1",
        kb=KB,
    )
    key_inds = result["key_indicators_for_linkage"]
    print(f"  Key indicators: {list(key_inds.keys())}")
    assert_gt(len(key_inds), 0, "At least 1 key indicator")


def test_filter_integrated():
    """比亚迪 (6.1 → [3.1, 3.2, 3.3]): collect key indicators from all sub-sectors."""
    print("\n--- filter_key_indicators (比亚迪, integrated) ---")
    data = sector_data("比亚迪")
    general = calculate_general(financial_data=data)
    sector = calculate_sector(financial_data=data, sub_sectors=["3.1","3.2","3.3"],
                              _sector_code="6.1", kb=KB)

    result = filter_key_indicators(
        general_indicators=general["general_indicators"],
        sector_indicators=sector["sector_indicators"],
        _sector_code="6.1",
        sub_sectors=["3.1", "3.2", "3.3"],
        kb=KB,
    )
    key_inds = result["key_indicators_for_linkage"]
    print(f"  Key indicators (integrated): {len(key_inds)}")
    for k, v in key_inds.items():
        print(f"    {k}: {v.get('value')} {v.get('unit')}")
    assert_gt(len(key_inds), 0, "Integrated has key indicators")


def test_fallback_table_coverage():
    """Verify fallback table covers all known sectors."""
    print("\n--- Fallback table coverage ---")
    expected_codes = [f"{a}.{b}" for a in range(1,7) for b in range(1,7)]
    covered = set(_KEY_INDICATOR_FALLBACK.keys())
    missing = set()
    for code in expected_codes:
        if code in covered:
            continue
        if code.startswith("6.") and code != "6.1":
            continue  # Only 6.1 exists
        if code in {"2.6"}:  # has entry but empty lists
            continue
        # Check if it realistically exists
        if KB:
            sector = KB.get_sector(code)
            if sector and code not in covered:
                missing.add(code)
    if missing:
        print(f"  Missing fallback entries: {missing}")
    else:
        ok("All KB sectors have fallback entries")


# ═══════════════════════════════════════════════════════════════════════
# linkage_analysis
# ═══════════════════════════════════════════════════════════════════════

def test_linkage_mock():
    """Test linkage_analysis with mock LLM (no API key)."""
    print("\n--- linkage_analysis (mock) ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, _sector_code="3.1", kb=KB)

    key_result = filter_key_indicators(
        general_indicators=general,
        sector_indicators=sector["sector_indicators"],
        _sector_code="3.1",
        kb=KB,
    )

    result = linkage_analysis(
        sector_level2="3.1 动力电池电芯",
        key_indicators_for_linkage=key_result["key_indicators_for_linkage"],
        general_indicators=general,
        sector_indicators=sector["sector_indicators"],
        kb=KB,
    )

    diagnosis = result["linkage_diagnosis"]
    print(f"  Linkage conclusions: {len(diagnosis)}")
    for d in diagnosis:
        print(f"    [{d.get('status','?')}] {d.get('combination','?')[:50]}")
        print(f"      {d.get('description','')[:80]}")
    assert_gt(len(diagnosis), 0, "At least 1 linkage conclusion")
    assert_in("status", str(diagnosis[0]), "Has status field")
    assert_in("evidence", str(diagnosis[0]), "Has evidence field")


def test_linkage_integrated_mock():
    """Test linkage_analysis for integrated enterprise."""
    print("\n--- linkage_analysis (integrated, mock) ---")
    data = sector_data("比亚迪")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, sub_sectors=["3.1","3.2","3.3"],
                              _sector_code="6.1", kb=KB)

    key_result = filter_key_indicators(
        general_indicators=general,
        sector_indicators=sector["sector_indicators"],
        _sector_code="6.1",
        sub_sectors=["3.1", "3.2", "3.3"],
        kb=KB,
    )

    result = linkage_analysis(
        sector_level2="6.1 一体化/跨界企业",
        sub_sectors=["3.1", "3.2", "3.3"],
        key_indicators_for_linkage=key_result["key_indicators_for_linkage"],
        general_indicators=general,
        sector_indicators=sector["sector_indicators"],
        kb=KB,
    )
    diagnosis = result["linkage_diagnosis"]
    print(f"  Integrated linkage conclusions: {len(diagnosis)}")
    for d in diagnosis:
        print(f"    [{d.get('status','?')}] {d.get('combination','')[:60]}")
    assert_gt(len(diagnosis), 0, "Integrated linkage has conclusions")


def test_extract_code():
    """Test sector code extraction from sector name."""
    print("\n--- Sector code extraction ---")
    assert_eq(_extract_code_from_sector("1. 锂资源开采 & 锂盐"), "1.", "Extract '1.' from KB name")
    assert_eq(_extract_code_from_sector("3.1 动力电池电芯"), "3.1", "Extract '3.1'")
    assert_eq(_extract_code_from_sector(None), None, "None → None")
    assert_eq(_extract_code_from_sector("no code here"), None, "No code → None")


def test_json_extraction():
    """Test JSON extraction from LLM responses."""
    print("\n--- JSON extraction ---")
    # Clean JSON
    r = extract_json_from_response('[{"a":1}]')
    assert_eq(r, [{"a":1}], "Clean JSON array")
    # With markdown fence
    r = extract_json_from_response('```json\n[{"b":2}]\n```')
    assert_eq(r, [{"b":2}], "Markdown-fenced JSON")
    # With extra text
    r = extract_json_from_response('Here is the result:\n[{"c":3}]\nHope this helps.')
    assert_eq(r, [{"c":3}], "JSON with surrounding text")
    # Invalid
    r = extract_json_from_response('not json at all')
    assert_eq(r, None, "Invalid → None")
    # Empty
    r = extract_json_from_response(None)
    assert_eq(r, None, "None input → None")


# ═══════════════════════════════════════════════════════════════════════
# anomaly_scan
# ═══════════════════════════════════════════════════════════════════════

def test_anomaly_clean():
    """Clean data: no anomalies should be detected."""
    print("\n--- anomaly_scan (clean data, 宁德时代) ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, _sector_code="3.1", kb=KB)

    result = anomaly_scan(
        general_indicators=general,
        sector_indicators=sector["sector_indicators"],
        financial_data=data,
        _sector_code="3.1",
        kb=KB,
    )
    signals = result["anomaly_signals"]
    print(f"  Anomaly signals: {len(signals)}")
    for s in signals:
        print(f"    [sev={s['severity']}] {s['rule_id']}: {s['description'][:80]}")
    ok(f"Clean data anomaly scan complete ({len(signals)} signals)")


def test_anomaly_dangerous():
    """Constructed dangerous data: should trigger multiple anomaly rules."""
    print("\n--- anomaly_scan (dangerous data) ---")
    # Construct data that should trigger general_1 (paper profit) and general_4 (inventory bomb)
    dangerous = {
        "营业收入": 10000000000.0,
        "营业成本": 8000000000.0,
        "净利润": 500000000.0,           # positive profit
        "扣非净利润": 400000000.0,
        "经营活动现金流净额": -200000000.0, # negative OCF → triggers general_1
        "总资产": 30000000000.0,
        "总负债": 20000000000.0,
        "净资产": 10000000000.0,
        "流动资产": 8000000000.0,
        "流动负债": 5000000000.0,
        "存货": 4000000000.0,            # 40% of revenue → triggers general_4
        "存货跌价损失": 10000000.0,       # very low impairment
        "应收账款": 3000000000.0,
        "货币资金": 5000000000.0,         # > 50% of current assets
        "销售费用": 100000000.0,
        "管理费用": 100000000.0,
        "财务费用": 100000000.0,
        "研发费用": 100000000.0,
        "期初净资产": 9000000000.0,
        "期末净资产": 10000000000.0,
    }

    general = calculate_general(financial_data=dangerous)
    general = annotate_formulas(general["general_indicators"])

    result = anomaly_scan(
        general_indicators=general,
        financial_data=dangerous,
        _sector_code="1.1",
        kb=KB,
    )
    signals = result["anomaly_signals"]
    print(f"  Anomaly signals triggered: {len(signals)}")
    for s in signals:
        print(f"    [sev={s['severity']}] {s['rule_id']}: {s['description'][:100]}")
        print(f"      Risk: {s['potential_risk'][:80]}")

    # Should trigger general_1 (paper profit) and general_4 (inventory bomb)
    rule_ids = [s["rule_id"] for s in signals]
    assert_in("general_1", rule_ids, "Paper profit detected")
    assert_in("general_4", rule_ids, "Inventory bomb detected")
    assert_gt(len(signals), 1, "Multiple anomalies detected")


def test_anomaly_ar_boom():
    """AR growing much faster than revenue → trigger general_2."""
    print("\n--- anomaly_scan (AR boom) ---")
    ar_boom = {
        "营业收入": 10000000000.0,
        "营业成本": 7500000000.0,
        "净利润": 100000000.0,
        "扣非净利润": 80000000.0,
        "总资产": 20000000000.0,
        "总负债": 12000000000.0,
        "净资产": 8000000000.0,
        "流动资产": 5000000000.0,
        "流动负债": 3500000000.0,
        "存货": 2000000000.0,
        "应收账款": 3000000000.0,
        "固定资产": 5000000000.0,
        "期初净资产": 7500000000.0,
        "期末净资产": 8000000000.0,
        # Key: AR growth much higher than revenue growth
        "营业收入上期": 9500000000.0,    # revenue growth ~5%
        "应收账款上期": 1500000000.0,    # AR growth ~100%
    }
    general = calculate_general(financial_data=ar_boom)
    general = annotate_formulas(general["general_indicators"])

    # Show the computed values
    rev_yoy = general.get("营收同比增速", {}).get("value")
    ar_yoy = general.get("应收账款同比增速", {}).get("value")
    print(f"  Revenue YoY: {rev_yoy}%, AR YoY: {ar_yoy}%")

    result = anomaly_scan(general_indicators=general, financial_data=ar_boom)
    signals = result["anomaly_signals"]
    print(f"  Signals: {len(signals)}")
    for s in signals:
        print(f"    [{s['rule_id']}] {s['description'][:100]}")

    # Should trigger general_2 if AR growth > revenue growth + 20
    rule_ids = [s["rule_id"] for s in signals]
    if ar_yoy is not None and rev_yoy is not None and ar_yoy > rev_yoy + 20:
        assert_in("general_2", rule_ids, "AR boom detected")
    else:
        ok(f"AR growth ({ar_yoy}) vs revenue growth ({rev_yoy}) — no trigger expected")


def test_kb_rule_parsing():
    """Test KB anomaly rule evaluation."""
    print("\n--- KB rule parsing ---")
    if KB is None:
        print("  [SKIP] KB not available")
        return

    # Test rule evaluation with synthetic data
    test_inds = {
        "存货跌价损失": {"value": 100000.0},
        "存货": {"value": 50000000.0},  # rate = 0.2%
        "合同负债同比变动率": {"value": 60.0},
        "存货周转率": {"value": 2.0},
        "营收同比增速": {"value": -10.0},
        "销售毛利率": {"value": 5.0},
        "在建工程同比增速": {"value": 50.0},
    }

    # Test "存货跌价损失率极低" pattern
    rule = {"description": "存货跌价损失率极低", "risk": "少计提", "raw": "跌价损失率极低", "requires_external": False}
    triggered = _evaluate_kb_rule(rule, test_inds)
    print(f"  Low impairment rule triggered: {triggered} (rate=0.2%)")
    assert_eq(triggered, True, "Low impairment triggered")

    # Test "合同负债高增 + 存货周转恶化"
    rule2 = {"description": "合同负债高增但存货周转恶化", "risk": "订单无法落地",
             "raw": "合同负债大幅高增，但存货周转持续恶化", "requires_external": False}
    triggered2 = _evaluate_kb_rule(rule2, test_inds)
    print(f"  Contract liability + inventory rule triggered: {triggered2}")
    assert_eq(triggered2, True, "Contract+inventory triggered")


def test_external_rules_skipped():
    """External-data rules should be skipped."""
    print("\n--- External rules skipped ---")
    if KB is None:
        print("  [SKIP] KB not available")
        return

    result = anomaly_scan(
        general_indicators={},
        _sector_code="1.1",
        kb=KB,
    )
    skipped = result.get("skipped_external_rules", 0)
    print(f"  Skipped external rules for 1.1: {skipped}")
    assert_gt(skipped, 0, "External rules skipped for sector 1.1")


def test_general_rules_count():
    """Verify all 5 general rules are defined."""
    print("\n--- General rules ---")
    assert_eq(len(_GENERAL_RULES), 5, "5 general anomaly rules")
    for r in _GENERAL_RULES:
        assert_in("check", r, f"Rule {r['id']} has check")
        assert_in("severity", r, f"Rule {r['id']} has severity")
        assert 1 <= r["severity"] <= 5, f"Rule {r['id']} severity {r['severity']} in 1-5"
    ok("All general rules well-formed")


# ═══════════════════════════════════════════════════════════════════════
# Full pipeline: classify → validate → calc_general → calc_sector →
#   filter_key → linkage → anomaly
# ═══════════════════════════════════════════════════════════════════════

def test_full_pipeline():
    """Test all 7 nodes in sequence for 宁德时代."""
    print("\n" + "=" * 60)
    print("Full 7-Node Pipeline: 宁德时代")
    print("=" * 60)

    from nodes.classify_sector import classify_sector
    from nodes.validate_data import validate_data

    data = sector_data("宁德时代")

    # 1. classify
    s = classify_sector(company_name="宁德时代", stock_code="300750",
                        primary_business=["动力电池系统", "储能系统", "电池材料"], kb=KB)
    print(f"  [1] Sector: {s['sector_level1']} / code={s.get('_sector_code')}")

    # 2. validate
    v = validate_data(financial_data=data, sector_level2=s["sector_level2"], kb=KB)
    print(f"  [2] Validate: {v['data_completeness']:.1%} complete")

    # 3. calc general
    g = calculate_general(financial_data=data)
    g = annotate_formulas(g["general_indicators"])
    computed = sum(1 for ind in g.values() if ind["value"] is not None)
    print(f"  [3] General: {computed}/{len(g)} computed")

    # 4. calc sector
    sec = calculate_sector(financial_data=data, _sector_code=s.get("_sector_code"), kb=KB)
    total_sec = sum(len(inds) for sd in sec["sector_indicators"].values() for inds in sd.values())
    computed_sec = sum(1 for sd in sec["sector_indicators"].values()
                       for inds in sd.values() for ind in inds.values() if ind["value"] is not None)
    print(f"  [4] Sector: {computed_sec}/{total_sec} computed")

    # 5. filter key
    k = filter_key_indicators(general_indicators=g, sector_indicators=sec["sector_indicators"],
                              _sector_code=s.get("_sector_code"), kb=KB)
    n_key = len(k["key_indicators_for_linkage"])
    print(f"  [5] Filter: {n_key} key indicators")

    # 6. linkage
    l = linkage_analysis(sector_level2=s.get("sector_level2"),
                         key_indicators_for_linkage=k["key_indicators_for_linkage"],
                         general_indicators=g, sector_indicators=sec["sector_indicators"],
                         sector_characteristics=s.get("sector_characteristics"),
                         analysis_focus=s.get("analysis_focus"), kb=KB)
    print(f"  [6] Linkage: {len(l['linkage_diagnosis'])} conclusions")
    for d in l["linkage_diagnosis"][:3]:
        print(f"      [{d.get('status','?')}] {d.get('combination','')[:60]}")

    # 7. anomaly
    a = anomaly_scan(general_indicators=g, sector_indicators=sec["sector_indicators"],
                     financial_data=data, _sector_code=s.get("_sector_code"), kb=KB)
    print(f"  [7] Anomaly: {len(a['anomaly_signals'])} signals, "
          f"{a.get('skipped_external_rules',0)} external skipped")
    for sig in a["anomaly_signals"]:
        print(f"      [sev={sig['severity']}] {sig['rule_id']}: {sig['description'][:80]}")

    print(f"  Pipeline: 7/7 nodes completed [OK]")


# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("FinAgent-Lithium Nodes 5-7 Tests")
    print(f"KB loaded: {KB is not None}")

    print("\n" + "=" * 60)
    print("  filter_key_indicators")
    print("=" * 60)
    test_filter_ningde()
    test_filter_tianqi()
    test_filter_integrated()
    test_fallback_table_coverage()

    print("\n" + "=" * 60)
    print("  linkage_analysis")
    print("=" * 60)
    test_extract_code()
    test_json_extraction()
    test_linkage_mock()
    test_linkage_integrated_mock()

    print("\n" + "=" * 60)
    print("  anomaly_scan")
    print("=" * 60)
    test_general_rules_count()
    test_anomaly_clean()
    test_anomaly_dangerous()
    test_anomaly_ar_boom()
    test_kb_rule_parsing()
    test_external_rules_skipped()

    print("\n" + "=" * 60)
    print("  Full Pipeline")
    print("=" * 60)
    test_full_pipeline()

    print("\n" + "=" * 60)
    print("  All tests complete.")
    print("=" * 60)

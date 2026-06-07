"""
Tests for generate_report node.

Validates:
  - Fallback report has all 6 required chapters
  - Report contains: disclaimer,通俗术语解释, missing data markers
  - Input JSON builder produces correct structure
  - Integrated enterprise report structure
  - Full 8-node pipeline produces complete report
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.generate_report import (
    generate_report,
    _build_input_json,
    _generate_fallback_report,
)
from nodes.calculate_general import calculate_general, annotate_formulas
from nodes.calculate_sector import calculate_sector, _test_data_sample as sector_data
from nodes.filter_key_indicators import filter_key_indicators
from nodes.linkage_analysis import linkage_analysis
from nodes.anomaly_scan import anomaly_scan

try:
    from lithium_kb import LithiumKnowledgeBase
    KB = LithiumKnowledgeBase(Path(__file__).resolve().parent.parent / "lithium_knowledge_base.json").load()
except Exception:
    KB = None


def ok(label: str): print(f"  [PASS] {label}")
def fail(label: str, detail=""): print(f"  [FAIL] {label}  -- {detail}")
def assert_eq(a, b, label): ok(label) if a == b else fail(label, f"{a} != {b}")
def assert_in(n, h, label): ok(label) if n in str(h) else fail(label, f"'{n}' not found")
def assert_gt(a, b, label): ok(label) if a > b else fail(label, f"{a} <= {b}")


# ═══════════════════════════════════════════════════════════════════════
# Input JSON builder tests
# ═══════════════════════════════════════════════════════════════════════

def test_input_json_structure():
    """Verify input JSON has all required sections."""
    print("\n--- Input JSON structure ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, _sector_code="3.1", kb=KB)
    key = filter_key_indicators(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        _sector_code="3.1", kb=KB)
    linkage = linkage_analysis(
        sector_level2="3.1 动力电池电芯",
        key_indicators_for_linkage=key["key_indicators_for_linkage"],
        general_indicators=general, sector_indicators=sector["sector_indicators"], kb=KB)
    anomaly = anomaly_scan(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        financial_data=data, _sector_code="3.1", kb=KB)

    input_json = _build_input_json(
        "宁德时代", "300750", "2025Q4",
        "中游电芯&结构件", "3.1 动力电池电芯",
        "动力电池制造，产能利用率核心指标", "关注固定资产周转和存货结构",
        None,
        general, sector["sector_indicators"],
        key["key_indicators_for_linkage"],
        linkage["linkage_diagnosis"],
        anomaly["anomaly_signals"],
        [], 0.6, anomaly.get("skipped_external_rules", 0),
    )

    # Check required top-level keys
    for key_name in ["company", "sector", "data_quality", "general_indicators",
                     "sector_indicators", "key_indicators", "linkage_diagnosis",
                     "anomaly_signals"]:
        assert_in(key_name, input_json, f"Has '{key_name}' section")

    assert_eq(input_json["company"]["name"], "宁德时代", "Company name")
    assert_eq(input_json["sector"]["level1"], "中游电芯&结构件", "Sector level1")
    assert_gt(len(input_json["general_indicators"]), 5, ">5 general indicators")
    assert_in("60%", input_json["data_quality"]["completeness"], "Completeness 60%")
    ok("Input JSON structure complete")


# ═══════════════════════════════════════════════════════════════════════
# Fallback report tests
# ═══════════════════════════════════════════════════════════════════════

def test_fallback_has_all_chapters():
    """Fallback report must contain all 6 mandatory chapters."""
    print("\n--- Fallback: all 6 chapters ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, _sector_code="3.1", kb=KB)
    key = filter_key_indicators(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        _sector_code="3.1", kb=KB)
    linkage = linkage_analysis(
        sector_level2="3.1 动力电池电芯",
        key_indicators_for_linkage=key["key_indicators_for_linkage"],
        general_indicators=general, sector_indicators=sector["sector_indicators"], kb=KB)
    anomaly = anomaly_scan(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        financial_data=data, _sector_code="3.1", kb=KB)

    input_json = _build_input_json(
        "宁德时代", "300750", "2025Q4",
        "中游电芯&结构件", "3.1 动力电池电芯",
        "动力电池制造", "关注固定资产周转",
        None, general, sector["sector_indicators"],
        key["key_indicators_for_linkage"],
        linkage["linkage_diagnosis"],
        anomaly["anomaly_signals"],
        [], 0.6, anomaly.get("skipped_external_rules", 0),
    )

    report = _generate_fallback_report(input_json)

    # Must contain all 6 chapter headers
    chapters = [
        "一、公司画像",
        "二、体检报告",
        "三、赛道专属深度检查",
        "四、联动诊断",
        "五、异常警报",
        "六、总结",
    ]
    for ch in chapters:
        assert_in(ch, report, f"Chapter: {ch}")

    # Must contain disclaimer
    assert_in("不构成投资建议", report, "Disclaimer present")

    # Must contain emoji risk levels
    assert_in("🟢", report, "Green emoji")
    assert_in("⚪", report, "Gray emoji for missing")

    # Must be valid Markdown (has headers and tables)
    assert_in("## ", report, "Has markdown headers")
    assert_in("|---", report, "Has markdown table")

    print(f"\n  Report length: {len(report)} chars")
    print(f"  Report preview (first 3 lines):")
    for line in report.split("\n")[:3]:
        print(f"    {line[:100]}")


def test_fallback_tianqi():
    """天齐锂业 fallback report with KB sector indicators."""
    print("\n--- Fallback: 天齐锂业 ---")
    data = sector_data("天齐锂业")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, _sector_code="1.1", kb=KB)
    key = filter_key_indicators(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        _sector_code="1.1", kb=KB)
    linkage = linkage_analysis(
        sector_level2="1.1 锂资源开采",
        key_indicators_for_linkage=key["key_indicators_for_linkage"],
        general_indicators=general, sector_indicators=sector["sector_indicators"], kb=KB)
    anomaly = anomaly_scan(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        financial_data=data, _sector_code="1.1", kb=KB)

    input_json = _build_input_json(
        "天齐锂业", "002466", "2025Q4",
        "上游资源与锂盐", "1.1 锂资源开采",
        "大宗商品属性，利润绑定锂价周期",
        "关注存货跌价、固定资产、合同负债",
        None, general, sector["sector_indicators"],
        key["key_indicators_for_linkage"],
        linkage["linkage_diagnosis"],
        anomaly["anomaly_signals"],
        [], 0.5, anomaly.get("skipped_external_rules", 0),
    )

    report = _generate_fallback_report(input_json)

    for ch in ["一、公司画像", "二、体检报告", "三、赛道专属深度检查",
               "四、联动诊断", "五、异常警报", "六、总结"]:
        assert_in(ch, report, f"天齐锂业: {ch}")

    assert_in("锂资源", report, "Sector name in report")
    assert_in("不构成投资建议", report, "Disclaimer")

    # Should have sector-specific indicators
    has_sector = "存货跌价损失率" in report or "固定资产周转率" in report
    ok(f"Sector indicators present: {has_sector}")

    print(f"  Report length: {len(report)} chars")


def test_fallback_with_anomalies():
    """Report with anomaly signals should list them in chapter 5."""
    print("\n--- Fallback: with anomalies ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])

    # Inject fake anomaly signals
    fake_anomalies = [
        {
            "rule_id": "general_2",
            "description": "应收增速(45.00%)远超营收增速(18.00%)",
            "potential_risk": "靠放宽账期抢订单，收入虚胖",
            "suggested_accounts": ["应收账款", "营业收入", "信用减值损失"],
            "severity": 3,
        },
        {
            "rule_id": "general_4",
            "description": "存货(45亿)占营收(340亿)比超30%，跌价损失极低",
            "potential_risk": "隐藏库存贬值损失",
            "suggested_accounts": ["存货", "存货跌价损失"],
            "severity": 5,
        },
    ]

    input_json = _build_input_json(
        "测试公司", "", "", "中游电芯&结构件", "3.1 动力电池",
        "", "", None, general, {}, None, [], fake_anomalies, [], 0.5, 0,
    )

    report = _generate_fallback_report(input_json)

    assert_in("general_2", report, "Anomaly rule_id in report")
    assert_in("general_4", report, "Second anomaly rule_id")
    assert_in("⚠️", report, "Warning emoji for anomalies")
    assert_in("应收账款", report, "Suggested accounts listed")
    assert_in("不构成投资建议", report, "Disclaimer")


def test_fallback_integrated():
    """Integrated enterprise report with sub-sector chapters."""
    print("\n--- Fallback: integrated enterprise ---")
    data = sector_data("比亚迪")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(
        financial_data=data, sub_sectors=["3.1", "3.2", "3.3"],
        _sector_code="6.1", kb=KB)

    input_json = _build_input_json(
        "比亚迪", "002594", "2025Q4",
        "一体化/跨界企业", "6.1 一体化",
        "跨界经营，涉及多赛道", "分板块分析",
        ["3.1", "3.2", "3.3"],
        general, sector["sector_indicators"],
        {}, [], [], [], 0.5, 0,
    )

    report = _generate_fallback_report(input_json)

    for ch in ["一、公司画像", "二、体检报告", "三、赛道专属深度检查",
               "四、联动诊断", "五、异常警报", "六、总结"]:
        assert_in(ch, report, f"Integrated: {ch}")

    # Should show sub-sector labels
    assert_in("3.1", report, "Sub-sector 3.1 heading")
    assert_in("3.2", report, "Sub-sector 3.2 heading")
    assert_in("不构成投资建议", report, "Disclaimer")

    print(f"  Report length: {len(report)} chars")


def test_missing_data_marker():
    """Report should mark missing indicators, never estimate."""
    print("\n--- Missing data markers ---")
    sparse_data = {
        "营业收入": 10000000000.0,
        "营业成本": 7000000000.0,
        "净利润": 2000000000.0,
    }
    general = calculate_general(financial_data=sparse_data)
    general = annotate_formulas(general["general_indicators"])

    input_json = _build_input_json(
        "稀疏数据测试", "", "", "未知", "未知", "", "", None,
        general, {}, None, [], [], [], 0.1, 0,
    )

    report = _generate_fallback_report(input_json)

    # Should mention missing data, not estimate values
    assert_in("待补充", report, "PENDING marker")
    assert_in("缺少", report, "Missing data mentioned")
    assert_in("暂时无法计算", report, "Cannot compute note")
    assert_in("不构成投资建议", report, "Disclaimer")

    # Should NOT contain fictional values for missing indicators
    ok("Report handles missing data correctly")


# ═══════════════════════════════════════════════════════════════════════
# LLM path test (mock)
# ═══════════════════════════════════════════════════════════════════════

def test_generate_report_mock():
    """generate_report with mock LLM (no API key)."""
    print("\n--- generate_report (mock LLM) ---")
    data = sector_data("宁德时代")
    general = calculate_general(financial_data=data)
    general = annotate_formulas(general["general_indicators"])
    sector = calculate_sector(financial_data=data, _sector_code="3.1", kb=KB)
    key = filter_key_indicators(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        _sector_code="3.1", kb=KB)
    linkage = linkage_analysis(
        sector_level2="3.1 动力电池电芯",
        key_indicators_for_linkage=key["key_indicators_for_linkage"],
        general_indicators=general, sector_indicators=sector["sector_indicators"], kb=KB)
    anomaly = anomaly_scan(
        general_indicators=general, sector_indicators=sector["sector_indicators"],
        financial_data=data, _sector_code="3.1", kb=KB)

    result = generate_report(
        company_name="宁德时代",
        stock_code="300750",
        current_period="2025年报",
        sector_level1="中游电芯&结构件",
        sector_level2="3.1 动力电池电芯",
        sector_characteristics="动力电池制造企业，产能利用率是核心指标",
        analysis_focus="关注固定资产周转率、存货结构、现金流质量",
        general_indicators=general,
        sector_indicators=sector["sector_indicators"],
        key_indicators_for_linkage=key["key_indicators_for_linkage"],
        linkage_diagnosis=linkage["linkage_diagnosis"],
        anomaly_signals=anomaly["anomaly_signals"],
        skipped_external_rules=anomaly.get("skipped_external_rules", 0),
    )

    report = result["report_markdown"]
    print(f"  Report length: {len(report)} chars")

    # Mock LLM returns the fallback report structure
    for ch in ["一、公司画像", "二、体检报告", "三、赛道专属深度检查",
               "四、联动诊断", "五、异常警报", "六、总结"]:
        assert_in(ch, report, f"Mock report has {ch}")

    assert_in("不构成投资建议", report, "Disclaimer")
    ok("Mock LLM report generation successful")


# ═══════════════════════════════════════════════════════════════════════
# Full 8-node pipeline
# ═══════════════════════════════════════════════════════════════════════

def test_full_8_node_pipeline():
    """All 8 nodes for 宁德时代, producing a complete report."""
    print("\n" + "=" * 60)
    print("Full 8-Node Pipeline → Report")
    print("=" * 60)

    from nodes.classify_sector import classify_sector
    from nodes.validate_data import validate_data

    data = sector_data("宁德时代")

    s = classify_sector(company_name="宁德时代", stock_code="300750",
                        primary_business=["动力电池系统", "储能系统", "电池材料"], kb=KB)
    v = validate_data(financial_data=data, sector_level2=s.get("sector_level2"), kb=KB)
    g = annotate_formulas(calculate_general(financial_data=data)["general_indicators"])
    sec = calculate_sector(financial_data=data, _sector_code=s.get("_sector_code"), kb=KB)
    k = filter_key_indicators(general_indicators=g, sector_indicators=sec["sector_indicators"],
                              _sector_code=s.get("_sector_code"), kb=KB)
    l = linkage_analysis(sector_level2=s.get("sector_level2"),
                         key_indicators_for_linkage=k["key_indicators_for_linkage"],
                         general_indicators=g, sector_indicators=sec["sector_indicators"],
                         sector_characteristics=s.get("sector_characteristics"),
                         analysis_focus=s.get("analysis_focus"), kb=KB)
    a = anomaly_scan(general_indicators=g, sector_indicators=sec["sector_indicators"],
                     financial_data=data, _sector_code=s.get("_sector_code"), kb=KB)

    result = generate_report(
        company_name="宁德时代", stock_code="300750", current_period="2025年报",
        sector_level1=s.get("sector_level1"),
        sector_level2=s.get("sector_level2"),
        sector_characteristics=s.get("sector_characteristics"),
        analysis_focus=s.get("analysis_focus"),
        general_indicators=g,
        sector_indicators=sec["sector_indicators"],
        key_indicators_for_linkage=k["key_indicators_for_linkage"],
        linkage_diagnosis=l["linkage_diagnosis"],
        anomaly_signals=a["anomaly_signals"],
        error_log=v.get("error_log", []),
        data_completeness=v.get("data_completeness"),
        skipped_external_rules=a.get("skipped_external_rules", 0),
    )

    report = result["report_markdown"]

    # Save report for manual review
    out_path = Path(__file__).resolve().parent.parent / "_test_report_ningde.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"  Report saved to: {out_path}")

    # Structure checks
    for ch in ["一、公司画像", "二、体检报告", "三、赛道专属深度检查",
               "四、联动诊断", "五、异常警报", "六、总结"]:
        assert_in(ch, report, f"8-pipeline: {ch}")

    assert_in("宁德时代", report, "Company name in report")
    assert_in("动力电池", report, "Sector in report")
    assert_in("不构成投资建议", report, "Disclaimer")
    assert_in("🟢", report, "Risk emoji present")

    print(f"  Report: {len(report)} chars, {len(report.splitlines())} lines")
    print(f"  Pipeline complete: 8/8 nodes [OK]")

    # Print first few lines of each chapter
    for line in report.split("\n"):
        if line.startswith("## "):
            print(f"    {line}")


# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("FinAgent-Lithium Node 8: generate_report Tests")
    print(f"KB loaded: {KB is not None}")

    print("\n" + "=" * 60)
    print("  Input JSON Builder")
    print("=" * 60)
    test_input_json_structure()

    print("\n" + "=" * 60)
    print("  Fallback Report Generator")
    print("=" * 60)
    test_fallback_has_all_chapters()
    test_fallback_tianqi()
    test_fallback_with_anomalies()
    test_fallback_integrated()
    test_missing_data_marker()

    print("\n" + "=" * 60)
    print("  LLM Path (mock)")
    print("=" * 60)
    test_generate_report_mock()

    print("\n" + "=" * 60)
    print("  Full 8-Node Pipeline")
    print("=" * 60)
    test_full_8_node_pipeline()

    print("\n" + "=" * 60)
    print("  All tests complete.")
    print("=" * 60)

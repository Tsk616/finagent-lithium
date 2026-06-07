"""
Unit tests for classify_sector and validate_data nodes.

Test cases:
  1. 宁德时代 — stock_code lookup → 3.1 (动力电池)
  2. 天齐锂业 — keyword matching → 1.1 (锂资源开采)
  3. 比亚迪    — stock_code lookup → 6.1 integrated (动力+储能+消费)

Also tests:
  - Manual fallback when keyword confidence < 0.6
  - Unknown company with no keywords and no manual_sector
  - Data validation: missing accounts, numeric sanity, alias resolution
  - Node chaining: classify_sector output → validate_data input
"""

import sys
import os
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.classify_sector import classify_sector, _score_sector
from nodes.validate_data import (
    validate_data,
    AccountResolver,
    _numeric_checks,
    _BUILTIN_ACCOUNT_MAPPING,
)

# Try loading KB for richer testing
try:
    from lithium_kb import LithiumKnowledgeBase
    _KB_PATH = Path(__file__).resolve().parent.parent / "lithium_knowledge_base.json"
    if _KB_PATH.exists():
        KB = LithiumKnowledgeBase(_KB_PATH).load()
    else:
        KB = None
except Exception:
    KB = None


def ok(label: str):
    print(f"  [PASS] {label}")


def fail(label: str, detail=""):
    print(f"  [FAIL] {label}  -- {detail}")


def assert_eq(actual, expected, label: str) -> bool:
    if actual == expected:
        ok(label)
        return True
    fail(label, f"expected={expected}, got={actual}")
    return False


def assert_in(needle, haystack, label: str) -> bool:
    if needle in haystack:
        ok(label)
        return True
    fail(label, f"'{needle}' not found in {haystack}")
    return False


# ═══════════════════════════════════════════════════════════════════════
# classify_sector tests
# ═══════════════════════════════════════════════════════════════════════

def test_ningde_times_lookup():
    """宁德时代: stock_code 300750 → sector 3.1 via lookup table."""
    print("\n--- Test: 宁德时代 (lookup table) ---")
    result = classify_sector(
        company_name="宁德时代",
        stock_code="300750",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        kb=KB,
    )
    assert_eq(result["_source"], "lookup_table", "Source is lookup_table")
    assert_eq(result["sector_level1"], "中游电芯&结构件", "Level-1 is 中游电芯&结构件")
    assert_in("动力电池", result.get("sector_level2", ""), "Level-2 contains 动力电池")
    assert_eq(result["sub_sectors"], None, "No sub_sectors for single-sector")
    print(f"  Result: level1={result['sector_level1']}, level2={result.get('sector_level2', '')[:40]}")


def test_tianqi_keyword():
    """天齐锂业: no stock_code in lookup → keyword matching → 1.1."""
    print("\n--- Test: 天齐锂业 (keyword matching) ---")
    result = classify_sector(
        company_name="天齐锂业",
        stock_code="",  # no stock code provided
        primary_business=["锂矿开采与销售", "碳酸锂及氢氧化锂生产"],
        kb=KB,
    )
    assert_in("keyword_matching", result["_source"],
              "Source is keyword_matching (or keyword_matching_low_confidence)")
    assert_eq(result["sector_level1"], "上游资源与锂盐", "Level-1 is 上游资源与锂盐")
    conf = result.get("_confidence", 0)
    print(f"  Result: level1={result['sector_level1']}, confidence={conf:.2f}")
    assert conf >= 0.2, f"Confidence >= 0.2"
    ok(f"Confidence {conf:.2f} >= 0.2")


def test_byd_integrated():
    """比亚迪: stock_code 002594 → 6.1 via lookup table, with sub_sectors."""
    print("\n--- Test: 比亚迪 (integrated) ---")
    result = classify_sector(
        company_name="比亚迪",
        stock_code="002594",
        primary_business=["新能源汽车", "动力电池", "储能系统", "消费电子"],
        kb=KB,
    )
    assert_eq(result["_source"], "lookup_table", "Source is lookup_table")
    assert_eq(result["sector_level1"], "一体化/跨界企业", "Level-1 is 一体化/跨界企业")
    sub = result.get("sub_sectors", [])
    assert_eq(len(sub), 3, "3 sub-sectors")
    assert_in("3.1", sub, "Sub-sector 3.1 (动力电池)")
    assert_in("3.2", sub, "Sub-sector 3.2 (储能电池)")
    assert_in("3.3", sub, "Sub-sector 3.3 (消费电池)")
    print(f"  Result: level1={result['sector_level1']}, sub_sectors={sub}")


def test_byd_keyword_integrated():
    """比亚迪 via keyword matching: multi-sector keywords → 6.1 integrated."""
    print("\n--- Test: 比亚迪 (keyword matching → integrated) ---")
    result = classify_sector(
        company_name="比亚迪",
        stock_code="",  # no lookup
        primary_business=["动力电池研发制造", "储能系统集成", "消费类电池"],
        kb=KB,
    )
    assert_eq(result["_source"], "keyword_matching_integrated", "Source is keyword_matching_integrated")
    assert_eq(result["sector_level1"], "一体化/跨界企业", "Level-1 is 一体化/跨界企业")
    sub = result.get("sub_sectors", [])
    assert len(sub) >= 2, f"At least 2 sub-sectors, got {len(sub)}"
    print(f"  Result: sub_sectors={sub}")


def test_manual_fallback():
    """Low-confidence keyword match → manual_sector fallback."""
    print("\n--- Test: Manual fallback ---")
    result = classify_sector(
        company_name="某不知名公司",
        stock_code="",
        primary_business=["贸易", "销售"],  # very generic, low confidence
        manual_sector="4.3",  # 电池贸易/经销
        kb=KB,
    )
    assert_eq(result["_source"], "manual_fallback", "Source is manual_fallback")
    assert_eq(result["sector_level1"], "下游应用&后市场", "Level-1 via manual fallback")
    print(f"  Result: level1={result['sector_level1']}, sector_code=4.3")


def test_unknown():
    """No lookup, no keywords, no manual → unknown."""
    print("\n--- Test: Unknown company ---")
    result = classify_sector(
        company_name="外星科技",
        stock_code="",
        primary_business=["不明业务"],
        manual_sector=None,
        kb=KB,
    )
    assert_eq(result["_source"], "unknown", "Source is unknown")
    assert_eq(result["sector_level1"], None, "No level-1 for unknown")
    print(f"  Result: {result}")


def test_keyword_scoring():
    """Test the keyword scoring function directly."""
    print("\n--- Test: Keyword scoring ---")
    matched, total = _score_sector(
        ["碳酸锂生产", "氢氧化锂销售", "锂矿开采"],
        ["碳酸锂", "氢氧化锂", "锂盐", "锂辉石", "锂精矿", "锂矿开采"],
        ["lithium carbonate", "lithium hydroxide"]
    )
    assert_eq(matched, 3, "3 Chinese keywords matched")
    assert_eq(total, 8, "8 total keywords (6 CN + 2 EN)")
    ok(f"Matched {matched}/{total} keywords")

    # English keyword test
    matched2, total2 = _score_sector(
        ["We produce LFP cathode materials for EV batteries"],
        ["磷酸铁锂", "正极", "三元正极", "LFP", "NCM", "NCA"],
        ["cathode", "LFP", "NCM", "cathode material"]
    )
    assert matched2 >= 1, f"At least 1 English keyword matched (LFP, cathode)"
    ok(f"English matching: {matched2}/{total2} keywords")


# ═══════════════════════════════════════════════════════════════════════
# validate_data tests
# ═══════════════════════════════════════════════════════════════════════

def test_account_resolver():
    """Test AccountResolver alias matching."""
    print("\n--- Test: AccountResolver ---")
    resolver = AccountResolver(KB)

    assert_eq(resolver.resolve("营业收入"), "营业收入", "Exact Chinese match")
    assert_eq(resolver.resolve("Revenue"), "营业收入", "English alias: Revenue")
    assert_eq(resolver.resolve("Total Revenue"), "营业收入", "English alias: Total Revenue")
    assert_eq(resolver.resolve("  营业总收入  "), "营业收入", "Whitespace-insensitive")
    assert_eq(resolver.resolve("INVENTORY"), "存货", "Case-insensitive: INVENTORY→存货")
    assert_eq(resolver.resolve("PP&E"), "固定资产", "Special alias: PP&E→固定资产")
    assert_eq(resolver.resolve("归母净利润"), "净利润", "Alias: 归母净利润→净利润")
    assert_eq(resolver.resolve("完全不存在的科目XYZ"), None, "Unknown account → None")

    assert resolver.is_recognized("营业收入")
    assert not resolver.is_recognized("火星币收入")
    ok("Recognized/unknown detection correct")

    # Batch coverage: every built-in alias should resolve to SOMETHING
    unresolved = []
    for canonical, aliases in _BUILTIN_ACCOUNT_MAPPING.items():
        for alias in aliases:
            got = resolver.resolve(alias)
            if got is None:
                unresolved.append(f"{alias} -> None (expected some canonical)")
    if unresolved:
        for u in unresolved[:5]:
            fail("Alias completely unresolved", u)
    else:
        ok(f"All {sum(len(v) for v in _BUILTIN_ACCOUNT_MAPPING.values())} built-in aliases resolve correctly")


def test_validate_ningde_complete():
    """宁德时代: complete data → no missing critical accounts."""
    print("\n--- Test: validate_data (宁德时代, complete) ---")
    from nodes.validate_data import _test_data_sample
    data = _test_data_sample("宁德时代")
    result = validate_data(
        financial_data=data,
        notes_data={},
        sector_level1="中游电芯&结构件",
        sector_level2="3.1 动力电池",
        kb=KB,
    )
    assert_eq(result["validated"], True, "No numeric anomalies")
    completeness = result["data_completeness"]
    print(f"  Completeness: {completeness:.1%}")
    print(f"  Error log ({len(result['error_log'])} entries):")
    for entry in result["error_log"][:5]:
        print(f"    {entry[:100]}")
    # With complete data, unrecognized should be 0
    assert_eq(len(result["unrecognized_accounts"]), 0, "All accounts recognized")


def test_validate_missing_accounts():
    """Sparse data → error_log lists missing accounts."""
    print("\n--- Test: validate_data (sparse data) ---")
    sparse = {
        "营业收入": 10000000000.0,
        "净利润": 1000000000.0,
        "Unknown Foreign Key": 500000.0,  # unrecognized
    }
    result = validate_data(
        financial_data=sparse,
        notes_data={},
        sector_level1="上游资源与锂盐",
        sector_level2="1.1 锂资源开采",
        kb=KB,
    )
    assert len(result["missing_accounts"]) > 0, "Has missing accounts"
    assert len(result["unrecognized_accounts"]) > 0, "Has unrecognized accounts"
    assert result["data_completeness"] < 0.5, "Completeness < 0.5"
    print(f"  Missing: {result['missing_accounts'][:5]}")
    print(f"  Unrecognized: {result['unrecognized_accounts']}")
    print(f"  Completeness: {result['data_completeness']:.1%}")
    for entry in result["error_log"][:5]:
        print(f"  {entry[:120]}")


def test_numeric_anomaly():
    """Negative revenue → numeric anomaly in error_log."""
    print("\n--- Test: validate_data (negative revenue) ---")
    bad_data = {
        "营业收入": -100000.0,
        "总资产": 500000.0,
        "总负债": 300000.0,
        "净资产": 200000.0,
        "流动资产": 150000.0,
        "流动负债": 200000.0,  # current liabilities > current assets
        "存货": 50000.0,
        "应收账款": 30000.0,
        "固定资产": 200000.0,
    }
    result = validate_data(
        financial_data=bad_data,
        notes_data={},
        sector_level1="中游四大主材+箔材辅材",
        kb=KB,
    )
    error_texts = " ".join(result["error_log"])
    assert_in("应为正数", error_texts, "Negative revenue flagged")
    assert_in("流动", error_texts, "Liquidity concern flagged")
    print(f"  Error log:")
    for entry in result["error_log"]:
        print(f"    {entry[:120]}")


def test_balance_sheet_identity():
    """Assets ≠ Liabilities + Equity → identity check warning."""
    print("\n--- Test: Balance sheet identity check ---")
    bad_balance = {
        "营业收入": 1000000.0,
        "总资产": 1000000.0,
        "总负债": 400000.0,
        "净资产": 500000.0,  # 400K + 500K = 900K ≠ 1M
        "流动资产": 600000.0,
        "流动负债": 300000.0,
    }
    result = validate_data(
        financial_data=bad_balance,
        notes_data={},
        sector_level1="中游电芯&结构件",
        kb=KB,
    )
    assert_in("恒等式", " ".join(result["error_log"]), "Accounting identity flagged")
    print(f"  Error log:")
    for entry in result["error_log"]:
        print(f"    {entry[:120]}")


# ═══════════════════════════════════════════════════════════════════════
# Node chaining test
# ═══════════════════════════════════════════════════════════════════════

def test_node_chaining():
    """classify_sector output → validate_data input: full chain for 宁德时代."""
    print("\n" + "=" * 60)
    print("Test: Node Chaining (宁德时代 full chain)")
    print("=" * 60)

    from nodes.validate_data import _test_data_sample

    # Node 1: classify
    sector_result = classify_sector(
        company_name="宁德时代",
        stock_code="300750",
        primary_business=["动力电池系统", "储能系统", "电池材料"],
        kb=KB,
    )
    print(f"\n  [Node 1 - classify_sector]")
    print(f"    Source: {sector_result['_source']}")
    print(f"    Level 1: {sector_result['sector_level1']}")
    print(f"    Level 2: {sector_result.get('sector_level2', '')[:60]}")
    print(f"    Focus: {sector_result.get('analysis_focus', '')[:80]}")

    # Node 2: validate
    data = _test_data_sample("宁德时代")
    validation_result = validate_data(
        financial_data=data,
        notes_data={},
        sector_level1=sector_result["sector_level1"],
        sector_level2=sector_result.get("sector_level2"),
        sub_sectors=sector_result.get("sub_sectors"),
        kb=KB,
    )
    print(f"\n  [Node 2 - validate_data]")
    print(f"    Validated: {validation_result['validated']}")
    print(f"    Completeness: {validation_result['data_completeness']:.1%}")
    print(f"    Missing: {len(validation_result['missing_accounts'])} accounts")
    print(f"    Unrecognized: {len(validation_result['unrecognized_accounts'])} accounts")
    print(f"    Error log entries: {len(validation_result['error_log'])}")
    for entry in validation_result["error_log"]:
        print(f"      {entry[:120]}")

    # Assertions
    assert_eq(sector_result["sector_level1"], "中游电芯&结构件", "Chained: sector_level1 correct")
    assert validation_result["validated"], "Chained: data valid"
    assert validation_result["data_completeness"] > 0.3, \
        f"Chained: data_completeness > 0.3 (actual={validation_result['data_completeness']:.1%})"

    print(f"\n  Chain: classify_sector -> validate_data [OK]")


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print("FinAgent-Lithium Node Tests")
    print(f"KB loaded: {KB is not None}")

    # classify_sector tests
    print("\n" + "=" * 60)
    print("  classify_sector")
    print("=" * 60)
    test_ningde_times_lookup()
    test_tianqi_keyword()
    test_byd_integrated()
    test_byd_keyword_integrated()
    test_manual_fallback()
    test_unknown()
    test_keyword_scoring()

    # validate_data tests
    print("\n" + "=" * 60)
    print("  validate_data")
    print("=" * 60)
    test_account_resolver()
    test_validate_ningde_complete()
    test_validate_missing_accounts()
    test_numeric_anomaly()
    test_balance_sheet_identity()

    # Integration
    test_node_chaining()

    print("\n" + "=" * 60)
    print("  All tests complete.")
    print("=" * 60)

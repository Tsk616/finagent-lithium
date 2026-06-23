"""
Tests for business-owned metric weights and weighted scoring.

These tests protect the metric governance contract:
business weights from the Excel supplement must drive canonical naming,
metric categorization, and explainable weighted scoring.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.calculate_general import calculate_general
from nodes.calculate_sector import _test_data_sample as sector_test_data
from nodes.metric_config import (
    build_industry_comparison,
    calculate_weighted_score,
    enrich_metric_results,
    find_metric_config,
    load_metric_config,
)


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


def assert_gt(actual, expected, label: str) -> bool:
    if actual > expected:
        ok(label)
        return True
    fail(label, f"expected > {expected}, got={actual}")
    return False


def assert_in(needle, haystack, label: str) -> bool:
    if needle in haystack:
        ok(label)
        return True
    fail(label, f"{needle!r} not found")
    return False


def test_metric_config_loads_excel_weights():
    """Business Excel supplement is available as a 20-sector weight config."""
    config = load_metric_config()

    assert_eq(len(config["sectors"]), 20, "Loads 20 sector weight groups")

    sector = config["sectors"]["1.1"]
    assert_eq(sector["name"], "1. 锂资源开采 & 锂盐（碳酸锂 / 氢氧化锂）", "Sector 1.1 name")

    metric = find_metric_config("1.1", "销售毛利率")
    assert_eq(metric["display_name"], "销售毛利率", "Gross margin display name")
    assert_eq(metric["weight"], 12.0, "Gross margin business weight")
    assert_eq(metric["metric_type"], "盈利能力", "Gross margin metric type")
    assert_in("锂价波动", metric["weight_logic"], "Weight logic retained")


def test_metric_config_resolves_canonical_aliases():
    """Canonical names absorb small naming differences without changing legacy keys."""
    metric = find_metric_config("1.1", "扣非ROE")

    assert_eq(metric["display_name"], "扣非 ROE", "Display name keeps business wording")
    assert_eq(metric["canonical_name"], "扣非 ROE", "Canonical name comes from config")
    assert_eq(metric["weight"], 2.0, "Alias lookup returns configured weight")


def test_enrich_metric_results_adds_weight_metadata():
    """Computed indicators keep existing keys but gain config metadata."""
    data = sector_test_data("天齐锂业")
    indicators = calculate_general(financial_data=data)["general_indicators"]

    enriched = enrich_metric_results("1.1", indicators)
    gross_margin = enriched["销售毛利率"]

    assert_eq(gross_margin["display_name"], "销售毛利率", "Display name attached")
    assert_eq(gross_margin["canonical_name"], "销售毛利率", "Canonical name attached")
    assert_eq(gross_margin["metric_category"], "通用", "Metric category attached")
    assert_eq(gross_margin["metric_type"], "盈利能力", "Metric type attached")
    assert_eq(gross_margin["weight"], 12.0, "Weight attached")
    assert_in("锂价波动", gross_margin["weight_logic"], "Weight logic attached")


def test_weighted_score_normalizes_available_metrics():
    """Missing indicators are skipped and remaining business weights are normalized."""
    indicators = {
        "销售毛利率": {"value": 30, "risk_level": "🟢正常", "weight": 12.0},
        "资产负债率": {"value": 75, "risk_level": "🔴高风险", "weight": 7.0},
        "营收同比增速": {"value": None, "risk_level": "⚪待补充", "weight": 5.0},
    }

    score = calculate_weighted_score(indicators)

    expected = round((100 * 12 + 20 * 7) / (12 + 7), 2)
    assert_eq(score["score"], expected, "Score normalizes over available weights")
    assert_eq(score["available_weight"], 19.0, "Available weight excludes pending")
    assert_eq(score["skipped_weight"], 5.0, "Skipped weight tracks pending data")
    assert_eq(len(score["components"]), 2, "Only available indicators contribute")
    assert_gt(len(score["skipped_components"]), 0, "Pending indicator is explainable")


def test_pipeline_exposes_weighted_score():
    """Pipeline state exposes the weighted score summary."""
    from web.workflow import run_pipeline

    data = sector_test_data("天齐锂业")
    state = run_pipeline(
        company_name="天齐锂业",
        stock_code="002466",
        current_period="2025年报",
        primary_business=["锂矿开采与销售", "碳酸锂及氢氧化锂生产"],
        financial_data=data,
    )

    score = state.get("weighted_score", {})
    assert_gt(score.get("score", 0), 0, "Pipeline emits weighted score")
    assert_gt(len(score.get("components", [])), 0, "Weighted score has components")
    assert_in("weighted_score", state, "State includes weighted_score")


def test_report_input_accepts_weighted_score():
    """Report input preserves weighted score so LLM/fallback can explain it."""
    from nodes.generate_report import _build_input_json

    score = {
        "score": 82.5,
        "available_weight": 80.0,
        "skipped_weight": 20.0,
        "components": [{"name": "销售毛利率", "score": 100.0, "weight": 12.0}],
        "skipped_components": [{"name": "营收同比增速", "weight": 5.0}],
    }

    data = _build_input_json(
        "测试公司", "", "2025年报",
        "上游资源与锂盐", "1.1 锂资源开采 & 锂盐",
        "", "", None, {}, {}, {}, [], [], [], 0.8, 0,
        weighted_score=score,
    )

    assert_in("weighted_score", data, "Input JSON includes weighted_score")
    assert_eq(data["weighted_score"]["score"], 82.5, "Score value preserved")
    assert_eq(data["weighted_score"]["available_weight"], 80.0, "Available weight preserved")


def test_industry_comparison_empty_without_peer_data():
    """No benchmark data returns explicit empty state, not fabricated numbers."""
    result = build_industry_comparison({}, [])

    assert_eq(result["available"], False, "No peers means unavailable")
    assert_in("No peer benchmark data", result["reason"], "Reason is explicit")
    assert_eq(result["comparisons"], [], "No fabricated comparisons")


def test_industry_comparison_uses_peer_financials():
    """Peer comparison only uses ratios derivable from supplied peer financials."""
    current = {
        "资产负债率": {
            "canonical_name": "资产负债率",
            "display_name": "资产负债率",
            "value": 60.0,
            "unit": "%",
            "weight": 7.0,
        }
    }
    peers = [
        {"name": "Peer A", "assets": 100.0, "equity": 50.0},
        {"name": "Peer B", "assets": 200.0, "equity": 80.0},
    ]

    result = build_industry_comparison(current, peers)

    assert_eq(result["available"], True, "Peer comparison available")
    assert_eq(len(result["comparisons"]), 1, "One comparable metric")
    row = result["comparisons"][0]
    assert_eq(row["metric"], "资产负债率", "Metric name retained")
    assert_eq(row["peer_average"], 55.0, "Peer average is derived")
    assert_eq(row["relation"], "worse_than_industry", "Higher debt ratio is worse")


if __name__ == "__main__":
    print("Metric config and weighted score tests")
    test_metric_config_loads_excel_weights()
    test_metric_config_resolves_canonical_aliases()
    test_enrich_metric_results_adds_weight_metadata()
    test_weighted_score_normalizes_available_metrics()
    test_pipeline_exposes_weighted_score()
    test_report_input_accepts_weighted_score()
    test_industry_comparison_empty_without_peer_data()
    test_industry_comparison_uses_peer_financials()

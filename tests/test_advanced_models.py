"""Tests for advanced_models — eight classic financial analysis models.

Verifies the business meaning (WHY a number matters), not just that numbers
come out. DuPont and EVA are deepened (multi-dimension + decomposition +
attribution + diagnosis); the rest are availability + degradation checks.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.advanced_models import (
    calculate_dupont, calculate_cvp, calculate_cashflow_health,
    calculate_zscore, calculate_dcf, calculate_relative_valuation,
    calculate_eva, harvard_framework_analysis, run_advanced_analysis,
)

# A healthy, profitable manufacturer (元 units). Loosely CATL-shaped.
HEALTHY = {
    "营业收入": 4.0e11, "营业成本": 3.0e11, "营业利润": 6.1e10, "利润总额": 6.0e10,
    "净利润": 5.0e10, "所得税费用": 1.0e10,
    "销售费用": 1.0e10, "管理费用": 1.5e10, "研发费用": 2.0e10, "财务费用": 2.0e9,
    "总资产": 7.0e11, "总负债": 4.5e11, "净资产": 2.5e11,
    "流动资产": 4.0e11, "流动负债": 3.0e11, "存货": 5.0e10,
    "未分配利润": 1.0e11, "盈余公积": 2.0e10,
    "货币资金": 8.0e10, "短期借款": 2.0e10, "长期借款": 3.0e10,
    "经营活动现金流净额": 6.0e10, "投资活动现金流净额": -4.0e10,
    "筹资活动现金流净额": -1.0e10, "购建固定资产支付的现金": 3.0e10,
}
MARKET = {"股价": 200.0, "总股本": 4.4e9, "总市值": 8.8e11, "市盈率": 17.6, "市净率": 3.5}
HISTORICAL = {
    "cagr": {"营业收入": {"value": 30.0}, "净利润": {"value": 25.0}},
    "metrics": {"营业收入": [3.0e11, 4.0e11], "净利润": [3.5e10, 5.0e10],
                "总资产": [6.0e11, 7.0e11], "净资产": [2.2e11, 2.5e11]},
}


def _by_name(result, name):
    for ind in result["indicators"]:
        if ind["name"] == name:
            return ind
    raise AssertionError(f"indicator {name} not found in {[i['name'] for i in result['indicators']]}")


# ── DuPont (deepened) ────────────────────────────────────────────────────

def test_dupont_has_profit_ladder_and_eight_indicators():
    r = calculate_dupont(HEALTHY)
    names = [i["name"] for i in r["indicators"]]
    # profit ladder: 毛利率 → 营业利润率 → 净利率 (each strictly smaller)
    gm = _by_name(r, "销售毛利率")["value"]
    nm = _by_name(r, "销售净利率")["value"]
    assert gm > nm  # gross margin always above net margin
    assert "总资产报酬率ROA" in names and "杠杆贡献度" in names
    assert len(r["indicators"]) == 8


def test_dupont_roe_reconciles_with_three_factors():
    r = calculate_dupont(HEALTHY)
    nm = r["chart"]["net_margin"] / 100
    at = r["chart"]["asset_turnover"]
    em = r["chart"]["equity_multiplier"]
    roe = r["chart"]["roe"] / 100
    assert abs(roe - nm * at * em) < 0.01


def test_dupont_five_factor_decomposition_present():
    r = calculate_dupont(HEALTHY)
    assert r["five_factor"]["available"]
    factor_names = [f["name"] for f in r["five_factor"]["factors"]]
    assert factor_names == ["税收负担率", "利息负担率", "EBIT利润率", "总资产周转率", "权益乘数"]


def test_dupont_driver_type_classified():
    r = calculate_dupont(HEALTHY)
    assert r["driver_type"] is not None
    assert r["driver_type"][0] in ("高利润型", "高周转型", "高杠杆型")


def test_dupont_trend_attribution_with_history():
    r = calculate_dupont(HEALTHY, HISTORICAL)
    assert r["trend"]["available"]
    assert r["trend"]["main_driver"] in ("净利率", "周转率", "权益乘数")


def test_dupont_trend_absent_without_history():
    r = calculate_dupont(HEALTHY)  # no historical
    assert r["trend"]["available"] is False


def test_dupont_diagnosis_is_substantive():
    r = calculate_dupont(HEALTHY)
    assert "ROE" in r["diagnosis"] and len(r["diagnosis"]) > 30


def test_dupont_degrades_without_equity():
    r = calculate_dupont({"营业收入": 100, "净利润": 10})
    assert _by_name(r, "净资产收益率ROE")["risk_color"] == "pending"


# ── CVP / Cash-flow / Z-score / DCF / Relative (availability + meaning) ───

def test_cvp_positive_safety_margin_and_discloses_approximation():
    r = calculate_cvp(HEALTHY)
    assert r["available"] and _by_name(r, "安全边际率")["value"] > 0
    assert "近似" in r["note"]


def test_cashflow_quality_high_when_ocf_exceeds_profit():
    r = calculate_cashflow_health(HEALTHY)
    assert _by_name(r, "净现比")["value"] > 1
    assert _by_name(r, "净现比")["risk_color"] == "normal"


def test_cashflow_flags_paper_profit():
    data = dict(HEALTHY, **{"经营活动现金流净额": 1.0e10})
    assert _by_name(calculate_cashflow_health(data), "净现比")["risk_color"] == "danger"


def test_zscore_listed_model_used_with_market_cap():
    # Market cap present → Altman listed-firm model (X4 = equity market cap / TL)
    r = calculate_zscore(HEALTHY, MARKET)
    assert "上市" in r["model"] and r["z_score"] is not None


def test_zscore_strong_firm_reads_safe():
    strong = calculate_zscore(HEALTHY, dict(MARKET, **{"总市值": 1.5e12}))
    assert strong["z_score"] > 2.99 and strong["chart"]["color"] == "normal"


def test_zscore_book_model_without_market_cap():
    r = calculate_zscore(HEALTHY, {})
    assert "账面" in r["model"] and r["available"]


def test_dcf_per_share_value_when_market_data_present():
    r = calculate_dcf(HEALTHY, MARKET, HISTORICAL)
    assert r["available"] and _by_name(r, "每股内在价值")["value"] is not None


def test_dcf_unavailable_when_fcf_negative():
    data = dict(HEALTHY, **{"经营活动现金流净额": 1.0e10, "购建固定资产支付的现金": 5.0e10})
    r = calculate_dcf(data, MARKET, HISTORICAL)
    assert r["available"] is False and "不适用" in r["note"]


def test_relative_valuation_derives_ps():
    r = calculate_relative_valuation(HEALTHY, MARKET, HISTORICAL)
    assert abs(_by_name(r, "市销率PS")["value"] - (8.8e11 / 4.0e11)) < 0.1


# ── EVA (deepened) ───────────────────────────────────────────────────────

def test_eva_has_roic_and_spread():
    r = calculate_eva(HEALTHY, MARKET)
    assert r["available"]
    assert r["roic_wacc"]["roic"] is not None
    # ROIC − WACC spread sign drives the verdict
    spread = r["roic_wacc"]["spread"]
    assert spread == round(r["roic_wacc"]["roic"] - r["roic_wacc"]["wacc"], 2)


def test_eva_value_creation_verdict_matches_spread():
    r = calculate_eva(HEALTHY, MARKET)
    if r["roic_wacc"]["spread"] > 0:
        assert r["roic_wacc"]["color"] == "normal"


def test_eva_mva_present_with_market_cap():
    r = calculate_eva(HEALTHY, MARKET)
    assert r["mva"]["available"] and r["mva"]["value"] is not None
    assert _by_name(r, "市场增加值MVA")["value"] is not None


def test_eva_mva_absent_without_market_cap():
    r = calculate_eva(HEALTHY, {})
    assert r["mva"]["available"] is False


def test_eva_effective_tax_rate_inferred():
    r = calculate_eva(HEALTHY, MARKET)
    # 所得税 10e9 / 利润总额 60e9 ≈ 16.7% → not the 25% default
    assert "16" in r["assumptions"]["所得税率"] or "17" in r["assumptions"]["所得税率"]


def test_eva_diagnosis_has_improvement_direction():
    r = calculate_eva(HEALTHY, MARKET)
    assert "改善方向" in r["diagnosis"]


def test_eva_eleven_indicators_with_mva():
    r = calculate_eva(HEALTHY, MARKET)
    assert len(r["indicators"]) == 11  # 10 core + MVA


# ── Harvard + orchestrator ───────────────────────────────────────────────

def test_harvard_rule_fallback_without_llm():
    state = {"company_name": "测试", "sector_level2": "锂电",
             "weighted_score": {"score": 75}, "anomaly_signals": []}
    r = harvard_framework_analysis(state, llm_config=None)
    assert r["available"] and r["source"] == "rule" and len(r["dimensions"]) == 4


def test_harvard_anomalies_lower_accounting_score():
    clean = harvard_framework_analysis({"weighted_score": {"score": 80}, "anomaly_signals": []}, None)
    dirty = harvard_framework_analysis(
        {"weighted_score": {"score": 80}, "anomaly_signals": [{"description": "x"}] * 3}, None)

    def acc(res):
        return next(d["score"] for d in res["dimensions"] if d["key"] == "accounting")
    assert acc(dirty) < acc(clean)


def test_run_advanced_analysis_assembles_four_sections():
    state = {"financial_data": HEALTHY, "market_data": MARKET, "historical_data": HISTORICAL}
    r = run_advanced_analysis(state, llm_config=None)
    assert set(r) >= {"business", "risk", "valuation", "strategy"}
    assert r["business"]["dupont"]["available"]
    assert r["business"]["dupont"]["trend"]["available"]  # historical wired through
    assert r["strategy"]["eva"]["available"]
    assert r["strategy"]["eva"]["roic_wacc"]["roic"] is not None

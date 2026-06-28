"""
Tests for the six-dimension financial prediction engine.

WHY: the trend-prediction section forecasts future ability scores from history.
These encode the contracts the report and charts depend on — scenario value
ordering, score bounds, dimension coverage, and graceful degradation when
history is too short — so a refactor can't silently produce nonsense forecasts.
"""
import pytest

from nodes.prediction_model import PredictionEngine, run_prediction_analysis


TRACK_WEIGHTS = {
    "销售毛利率": {"weight": 12, "category": "通用"},
    "扣非销售净利率": {"weight": 10, "category": "通用"},
    "资产负债率": {"weight": 7, "category": "通用"},
    "速动比率": {"weight": 6, "category": "通用"},
    "营收同比增速": {"weight": 5, "category": "通用"},
    "存货周转率": {"weight": 6, "category": "特殊"},
}

HISTORY = {
    "销售毛利率": [45.0, 25.0, 15.0, 18.0],
    "扣非销售净利率": [35.0, 12.0, 5.0, 8.0],
    "资产负债率": [40.0, 50.0, 58.0, 55.0],
    "速动比率": [1.2, 1.0, 0.8, 0.9],
    "营收同比增速": [80.0, 30.0, -10.0, 35.0],
    "存货周转率": [6.0, 4.0, 2.5, 4.5],
}


def test_linear_trend_extrapolates_next_point():
    eng = PredictionEngine(TRACK_WEIGHTS, cycle_year="2026")
    # Perfect line 10,20,30,40 -> next ~50
    assert eng._linear_trend_predict([10, 20, 30, 40]) == pytest.approx(50, abs=1e-6)


def test_predict_indicator_scenario_and_ci_ordering():
    eng = PredictionEngine(TRACK_WEIGHTS, cycle_year="2026")
    pred = eng._predict_indicator("销售毛利率", [45.0, 25.0, 15.0, 18.0])
    assert pred["pessimistic"] <= pred["cycle_adjusted"] <= pred["optimistic"]
    lo, hi = pred["confidence_interval"]
    assert lo <= pred["cycle_adjusted"] <= hi


def test_value_to_score_higher_better_monotonic_and_bounded():
    eng = PredictionEngine(TRACK_WEIGHTS, cycle_year="2026")
    low = eng._value_to_score("销售毛利率", 2.0)
    high = eng._value_to_score("销售毛利率", 35.0)
    assert 0 <= low <= 100 and 0 <= high <= 100
    assert high > low


def test_value_to_score_lower_better_inverted():
    eng = PredictionEngine(TRACK_WEIGHTS, cycle_year="2026")
    # 资产负债率: lower is better
    assert eng._value_to_score("资产负债率", 40.0) > eng._value_to_score("资产负债率", 95.0)


def test_predict_abilities_covers_six_dimensions_with_bounded_scores():
    eng = PredictionEngine(TRACK_WEIGHTS, cycle_year="2026")
    result = eng.predict_abilities(HISTORY, scenario="baseline")
    for dim in ["盈利能力", "运营能力", "成长能力", "偿债能力", "现金能力", "综合财务能力"]:
        assert dim in result
        assert 0 <= result[dim]["predicted_score"] <= 100


def test_derive_ratios_includes_liquidity_and_turnover():
    from nodes.historical_data import _derive_ratios
    metrics = {
        "营业收入": [1000.0, 1200.0], "营业成本": [600.0, 720.0],
        "净利润": [100.0, 120.0], "总资产": [2000.0, 2200.0],
        "总负债": [800.0, 900.0], "净资产": [1200.0, 1300.0],
        "流动资产": [800.0, 900.0], "流动负债": [400.0, 450.0],
        "存货": [200.0, 240.0], "应收账款": [250.0, 300.0],
        "固定资产": [500.0, 550.0],
    }
    dr = _derive_ratios(metrics, 2)  # values rounded to 2 decimals
    assert dr["流动比率"][1] == pytest.approx(2.0, abs=0.01)
    assert dr["速动比率"][1] == pytest.approx((900 - 240) / 450, abs=0.01)
    assert dr["存货周转率"][1] == pytest.approx(720 / 240, abs=0.01)
    assert dr["应收账款周转率"][1] == pytest.approx(1200 / 300, abs=0.01)
    assert dr["固定资产周转率"][1] == pytest.approx(1200 / 550, abs=0.01)


def test_build_prediction_input_computes_yoy_and_cash_quality():
    from nodes.prediction_model import _build_prediction_input
    historical = {
        "periods": ["2023年报", "2024年报", "2025年报"],
        "metrics": {
            "营业收入": [1000.0, 1200.0, 1500.0],
            "净利润": [100.0, 120.0, 150.0],
            "研发费用": [50.0, 60.0, 75.0],
            "经营活动现金流净额": [80.0, 100.0, 140.0],
        },
        "derived_ratios": {"销售毛利率": [20.0, 22.0, 25.0]},
    }
    out = _build_prediction_input(historical)
    assert "营收同比增速" in out and len(out["营收同比增速"]) == 2
    assert out["营收同比增速"][0] == pytest.approx(20.0)  # 1000->1200
    assert "研发费用率" in out and out["研发费用率"][-1] == pytest.approx(5.0)  # 75/1500
    assert "净利润现金含量" in out  # 经营现金流/净利润
    assert out["销售毛利率"] == [20.0, 22.0, 25.0]


def test_run_prediction_analysis_insufficient_history_degrades():
    # Only one period -> cannot forecast.
    state = {
        "_sector_code": None,
        "historical_data": {"periods": ["2025年报"], "metrics": {}, "derived_ratios": {}},
    }
    out = run_prediction_analysis(state, llm_config={"api_key": ""})
    assert out["available"] is False

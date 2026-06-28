"""
Regression test for the ratio-card sparkline render.

WHY: Jinja resolves ``r.sparkline.values`` via getattr first, which on a dict
returns the built-in ``.values`` *method* (not the "values" key), and tojson
then raises, 500-ing every report that actually has data. Reports only render
sparklines when financial data exists, so this stayed hidden while data-ful
analyses were dying with ERR_CONNECTION_CLOSED upstream.
"""
def test_ratio_card_sparkline_serializes_values(app):
    with app.app_context():
        from flask import render_template
        core_ratios = [{
            "name": "销售毛利率", "icon": "📈", "value": 25.0,
            "risk_level": "正常", "risk_color": "normal",
            "normal_range": "", "formula": "",
            "sparkline": {"labels": ["2023", "2024"], "values": [20.5, 25.0]},
        }]
        html = render_template("partials/_ratio_cards.html", core_ratios=core_ratios)
    # The real numeric values must reach the DOM, proving 'values' resolved to
    # the list and not the dict's built-in method.
    assert "20.5" in html
    assert "25.0" in html


def test_compare_table_iterates_row_values(app):
    """Same dict-method pitfall on /compare: metric_table rows are dicts with a
    'values' key, so `row.values` must mean the cell list, not dict.values()."""
    comparison = {
        "peer_count": 2,
        "available_metric_count": 1,
        "metric_table": [{
            "metric": "毛利率", "unit": "%",
            "best_company": "甲", "worst_company": "乙",
            "values": [
                {"company": "甲", "value": 25.0, "risk_level": "正常", "is_best": True, "is_worst": False},
                {"company": "乙", "value": 15.0, "risk_level": "警惕", "is_best": False, "is_worst": True},
            ],
        }],
        "radar": [
            {"company": "甲", "points": [{"metric": "毛利率", "score": 90}]},
            {"company": "乙", "points": [{"metric": "毛利率", "score": 40}]},
        ],
    }
    peers = [{"company": "甲"}, {"company": "乙"}]
    with app.app_context():
        from flask import render_template
        html = render_template("compare.html", comparison=comparison, peers=peers)
    assert "25.00" in html  # cell value rendered via %.2f
    assert "15.00" in html


def _sample_prediction():
    def ab(b, o, p):
        return {"baseline": b, "optimistic": o, "pessimistic": p,
                "ci_low": p - 4, "ci_high": o + 4, "mc_mean": b, "mc_std": 6.0,
                "prob_above_80": 5.0, "prob_above_60": 60.0, "prob_below_40": 3.0}
    return {
        "available": True, "target_year": 2026, "cycle_phase": "复苏期",
        "history_years": 4, "weight_split": {"general_pct": 64.0, "special_pct": 36.0},
        "abilities": {
            "盈利能力": ab(62.0, 70.0, 54.0), "运营能力": ab(58.0, 66.0, 50.0),
            "成长能力": ab(60.0, 72.0, 48.0), "偿债能力": ab(65.0, 71.0, 59.0),
            "现金能力": ab(55.0, 63.0, 47.0), "综合财务能力": ab(60.5, 68.0, 52.0),
        },
        "indicator_table": [{
            "ability": "盈利能力", "indicator": "销售毛利率",
            "historical_values": [20.0, 22.0, 25.0], "predicted_value": 26.5,
            "confidence_interval": [24.0, 29.0], "method": "trend_cycle",
            "raw_score": 85.0, "weight": 12.0, "category": "通用",
        }],
        "narrative": {
            "scenario_summary": "综合来看公司处于复苏通道。",
            "baseline": "维持稳健", "optimistic": "锂价回升超预期",
            "pessimistic": "需求不及预期", "key_risks": ["锂价下行"],
            "tracking_suggestions": ["关注存货周转"],
        },
    }


def test_prediction_partial_renders_without_jinja_error(app):
    """The prediction section must render with real values (and dict access must
    use subscripts, not the .values/.items pitfall)."""
    with app.app_context():
        from flask import render_template
        html = render_template("partials/_prediction.html",
                               prediction=_sample_prediction(), has_prediction=True)
    assert "销售毛利率" in html
    assert "26.5" in html       # predicted value reaches the DOM
    assert "复苏期" in html      # cycle phase shown
    assert "2026" in html        # target year

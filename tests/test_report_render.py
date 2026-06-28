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

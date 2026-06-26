"""
Tests for Wind quota protection, structured interpretations, and report UI data.

These tests intentionally avoid live Wind calls. Wind has a daily quota, so
automated tests must validate behavior at the adapter boundary with mocks.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_wind_skill_dir_prefers_env_path():
    """Wind skill discovery uses configured paths before any hardcoded default."""
    from nodes import wind_adapter

    with tempfile.TemporaryDirectory() as tmp:
        skill_dir = Path(tmp) / "wind-mcp-skill"
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "cli.mjs").write_text("// fixture", encoding="utf-8")

        with patch.dict(os.environ, {"WIND_SKILL_DIR": str(skill_dir)}):
            resolved = wind_adapter._resolve_wind_skill_dir()

    assert resolved == str(skill_dir)


def test_wind_cli_disabled_by_default_in_tests():
    """Tests should not spend Wind quota when no live-test flag is present."""
    from nodes import wind_adapter

    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "quota_guard"}, clear=False):
        with patch.object(wind_adapter.subprocess, "run") as run_mock:
            result = wind_adapter._call_wind_cli(
                "stock_data",
                "get_stock_basicinfo",
                {"question": "300750.SZ公司基本档案"},
                timeout=1,
            )
            status = wind_adapter.get_wind_status()

    assert result is None
    assert run_mock.call_count == 0
    assert status["live_calls_enabled"] is False
    assert status["daily_limit"] == 500


def test_wind_status_reports_api_key_configured_without_exposing_secret():
    """Wind status may report key presence, but must never expose the key."""
    from nodes import wind_adapter

    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(
            os.environ,
            {
                "WIND_CACHE_DIR": tmp,
                "WIND_API_KEY": "test-secret-key",
            },
            clear=False,
        ):
            status = wind_adapter.get_wind_status()

    assert status["api_key_configured"] is True
    assert "test-secret-key" not in str(status)


def test_wind_cli_uses_cache_before_subprocess():
    """A cached Wind response must be returned without invoking the CLI."""
    from nodes import wind_adapter

    cached = {"content": [{"text": "cached-company-info"}]}
    with tempfile.TemporaryDirectory() as tmp:
        with patch.dict(
            os.environ,
            {
                "WIND_CACHE_DIR": tmp,
                "WIND_LIVE_TEST": "1",
            },
            clear=False,
        ):
            wind_adapter._write_wind_cache(
                "stock_data",
                "get_stock_basicinfo",
                {"question": "300750.SZ公司基本档案"},
                cached,
            )
            with patch.object(wind_adapter.subprocess, "run") as run_mock:
                result = wind_adapter._call_wind_cli(
                    "stock_data",
                    "get_stock_basicinfo",
                    {"question": "300750.SZ公司基本档案"},
                    timeout=1,
                )

    assert result == cached
    assert run_mock.call_count == 0


def test_wind_only_financials_do_not_fallback_to_akshare():
    """Wind failures should return missing data instead of spending time on other APIs."""
    from nodes import wind_adapter

    with patch.object(wind_adapter, "_wind_fetch_financials", return_value=None):
        with patch.object(wind_adapter, "_ak_fetch_financials") as ak_fetch:
            result = wind_adapter.fetch_financials("002594.SZ", period="2025年报", timeout=1)

    assert result is None
    assert ak_fetch.call_count == 0


def test_wind_only_macro_context_does_not_fallback_to_akshare():
    """Macro context should expose missing data instead of using non-Wind sources."""
    from nodes import wind_adapter

    with patch.object(wind_adapter, "_wind_fetch_macro_context", return_value=None):
        with patch.object(wind_adapter, "_ak_fetch_macro_context") as ak_fetch:
            result = wind_adapter.fetch_macro_context(timeout=1)

    assert result is None
    assert ak_fetch.call_count == 0


def test_wind_financial_questions_are_short_and_bounded():
    """Financial fetching should use a small number of concise Wind questions."""
    from nodes import wind_adapter

    questions = wind_adapter._build_fundamentals_questions("002594.SZ", "2025")

    assert len(questions) == 3
    assert all("002594.SZ" in q for q in questions)
    assert all(len(q) < 150 for q in questions)
    assert "营业收入营业成本研发费用" not in questions[0]
    assert "期初净资产" in questions[2]
    assert "合同负债" in questions[1]
    assert "期初在建工程" in questions[2]


def test_integrated_completeness_normalizes_required_accounts():
    """Completeness should compare canonical accounts, not raw KB aliases/fragments."""
    from lithium_kb import LithiumKnowledgeBase
    from nodes.validate_data import validate_data

    kb = LithiumKnowledgeBase(Path("lithium_knowledge_base.json")).load()
    wind_like_keys = [
        "营业收入", "营业成本", "净利润", "扣非净利润", "研发费用", "销售费用",
        "管理费用", "财务费用", "经营活动现金流净额", "总资产", "总负债",
        "净资产", "流动资产", "流动负债", "应收账款", "存货", "固定资产",
        "在建工程", "合同负债", "应付账款", "营业收入上期", "期初应收账款",
        "期末应收账款", "期初存货", "期末存货", "期初净资产", "期末净资产",
        "期初在建工程", "期末在建工程", "期初合同负债", "期末合同负债",
    ]
    data = {key: 1.0 for key in wind_like_keys}

    result = validate_data(
        data,
        sector_level2="6.1 一体化/跨界企业",
        sub_sectors=["3.1", "3.2", "3.3"],
        kb=kb,
    )

    assert result["data_completeness"] > 0.8
    assert "过去" not in result["missing_accounts"]
    assert "存货周转率" not in result["missing_accounts"]
    assert "上期营收" not in result["missing_accounts"]


def test_deepseek_anthropic_client_uses_x_api_key_header():
    """DeepSeek Anthropic-compatible API expects x-api-key, not Bearer auth."""
    from nodes import llm_client

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"content": [{"type": "text", "text": "ok"}]}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return FakeResponse()

    with patch.dict(sys.modules, {"requests": FakeRequests}):
        result = llm_client.call_llm(
            "system",
            "user",
            config={
                "api_key": "test-key",
                "base_url": "https://api.deepseek.com/anthropic",
                "model": "deepseek-v4-flash",
                "max_tokens": 20,
                "temperature": 0,
                "timeout": 1,
            },
        )

    assert result == "ok"
    assert calls[0]["headers"]["x-api-key"] == "test-key"
    assert "Authorization" not in calls[0]["headers"]


def test_deepseek_openai_client_uses_chat_completions():
    """Default DeepSeek API should use OpenAI-compatible chat completions."""
    from nodes import llm_client

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok-chat"}}]}

    class FakeRequests:
        @staticmethod
        def post(url, headers, json, timeout):
            calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return FakeResponse()

    with patch.dict(sys.modules, {"requests": FakeRequests}):
        result = llm_client.call_llm(
            "system",
            "user",
            config={
                "api_key": "test-key",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "max_tokens": 20,
                "temperature": 0,
                "timeout": 1,
            },
        )

    assert result == "ok-chat"
    assert calls[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert "x-api-key" not in calls[0]["headers"]
    assert calls[0]["json"]["messages"][0]["role"] == "system"


def test_wind_financial_parser_handles_markdown_and_json_shapes():
    """Wind responses may arrive as markdown tables or nested JSON envelopes."""
    from nodes import wind_adapter

    table = """
| 科目 | 数值 |
| --- | --- |
| 营业收入 | 7771.02亿元 |
| 营业成本 | 6300.50亿元 |
| 净利润 | 402.54亿元 |
"""
    parsed_table = wind_adapter._parse_wind_financial_text(table)

    assert round(parsed_table["营业收入"] / 100000000, 2) == 7771.02
    assert round(parsed_table["营业成本"] / 100000000, 2) == 6300.50
    assert round(parsed_table["净利润"] / 100000000, 2) == 402.54

    nested_json = '{"data":[{"科目":"总资产","数值":"8000亿元"},{"指标":"总负债","值":"5100亿元"},{"name":"存货","value":"900亿元"}]}'
    parsed_json = wind_adapter._parse_wind_financial_text(nested_json)

    assert round(parsed_json["总资产"] / 100000000, 2) == 8000.0
    assert round(parsed_json["总负债"] / 100000000, 2) == 5100.0
    assert round(parsed_json["存货"] / 100000000, 2) == 900.0


def test_wind_result_extractor_accepts_structured_payloads():
    """Some Wind tools return structured data instead of content[0].text."""
    from nodes import wind_adapter

    payload = {"data": [{"科目": "营业收入", "数值": "7771亿元"}]}

    extracted = wind_adapter._extract_text_from_mcp_result(payload)
    parsed = wind_adapter._parse_wind_financial_text(extracted)

    assert round(parsed["营业收入"] / 100000000, 2) == 7771.0


def test_wind_financial_parser_handles_columnar_table_payload():
    """Wind may return tables as columns metadata plus row arrays."""
    from nodes import wind_adapter

    text = """
{
  "data": {
    "data": [{
      "columns": [
        {"name": "Wind代码", "type": "string"},
        {"name": "证券简称", "type": "string"},
        {"name": "2025年报资产总计", "type": "number", "unit": "亿元"},
        {"name": "2025年报负债合计", "type": "number", "unit": "亿元"},
        {"name": "2025年报净资产", "type": "number", "unit": "亿元"}
      ],
      "data": [["002594.SZ", "比亚迪", 8000, 5100, 2900]]
    }]
  }
}
"""

    parsed = wind_adapter._parse_wind_financial_text(text)

    assert round(parsed["总资产"] / 100000000, 2) == 8000.0
    assert round(parsed["总负债"] / 100000000, 2) == 5100.0
    assert round(parsed["净资产"] / 100000000, 2) == 2900.0


def test_wind_financial_parser_maps_prior_period_columns():
    """Prior-year Wind columns should feed growth and average-equity indicators."""
    from nodes import wind_adapter

    text = """
{
  "data": {
    "columns": [
      {"name": "2025年报营业收入", "unit": "亿元"},
      {"name": "2024年报营业收入", "unit": "亿元"},
      {"name": "2025年报应收账款", "unit": "亿元"},
      {"name": "2024年报应收账款", "unit": "亿元"},
      {"name": "2025年报存货", "unit": "亿元"},
      {"name": "2024年报存货", "unit": "亿元"},
      {"name": "2025年报净资产", "unit": "亿元"},
      {"name": "2024年报净资产", "unit": "亿元"}
    ],
    "data": [[7771, 6023, 700, 650, 900, 800, 2900, 2500]]
  }
}
"""

    parsed = wind_adapter._parse_wind_financial_text(text, period_year=2025)

    assert round(parsed["营业收入"] / 100000000, 2) == 7771.0
    assert round(parsed["上期营业收入"] / 100000000, 2) == 6023.0
    assert round(parsed["期初应收账款"] / 100000000, 2) == 650.0
    assert round(parsed["期初存货"] / 100000000, 2) == 800.0
    assert round(parsed["期初净资产"] / 100000000, 2) == 2500.0
    assert round(parsed["期末净资产"] / 100000000, 2) == 2900.0


def test_wind_fetch_financials_merges_bounded_mocked_calls():
    """Bounded Wind calls should merge score-critical financial accounts."""
    from nodes import wind_adapter

    mocked = [
        {"content": [{"text": "| 科目 | 数值 |\n| --- | --- |\n| 营业收入 | 7771亿元 |\n| 营业成本 | 6300亿元 |\n| 净利润 | 402亿元 |"}]},
        {"content": [{"text": '{"data":[{"科目":"总资产","数值":"8000亿元"},{"科目":"总负债","数值":"5100亿元"},{"科目":"存货","数值":"900亿元"}]}'}]},
        {"content": [{"text": '{"data":[{"科目":"上期营业收入","数值":"6023亿元"},{"科目":"期初净资产","数值":"2500亿元"},{"科目":"扣非净利润","数值":"360亿元"}]}'}]},
    ]

    with patch.object(wind_adapter, "_call_wind_cli", side_effect=mocked) as call_mock:
        result = wind_adapter._wind_fetch_financials("002594.SZ", "2025", timeout=1)

    assert call_mock.call_count == 3
    assert result["营业收入"] is not None
    assert result["总资产"] is not None
    assert result["上期营业收入"] is not None
    assert result["期初净资产"] is not None
    assert result["扣非净利润"] is not None
    assert result["毛利"] is not None


def test_structured_interpretations_do_not_invent_missing_data():
    """Interpretations should be evidence-backed and explicit about missing data."""
    from nodes.interpretation import build_structured_interpretations

    result = build_structured_interpretations(
        general_indicators={
            "Gross Margin": {
                "display_name": "Gross Margin",
                "value": 18.5,
                "unit": "%",
                "risk_level": "warning",
                "metric_type": "profitability",
                "single_mapping": "Shows product profitability.",
            },
            "Revenue Growth": {
                "display_name": "Revenue Growth",
                "value": None,
                "unit": "%",
                "risk_level": "pending",
                "metric_type": "growth",
                "single_mapping": "Needs prior-period revenue.",
            },
        },
        sector_indicators={},
        anomaly_signals=[],
        macro_context={},
        industry_comparison={"available": False, "reason": "No peer benchmark data", "comparisons": []},
        weighted_score={"score": 78.2, "components": []},
        llm_config={"api_key": ""},
    )

    metric_items = result["metric_interpretations"]
    assert any(item["metric"] == "Gross Margin" for item in metric_items)
    missing = [item for item in metric_items if item["metric"] == "Revenue Growth"][0]
    assert missing["status"] == "missing_data"
    assert "数据不足" in missing["interpretation"] or "insufficient" in missing["interpretation"].lower()
    assert result["industry_benchmark_insights"]["available"] is False


def test_template_data_exposes_grouped_metric_settings_and_insights():
    """Report templates need grouped settings and model insights without recomputing them."""
    from web.app import _build_template_data

    state = {
        "company_name": "TestCo",
        "general_indicators": {
            "Gross Margin": {
                "value": 18.5,
                "unit": "%",
                "risk_level": "warning",
                "display_name": "Gross Margin",
                "canonical_name": "Gross Margin",
                "metric_type": "profitability",
                "weight": 12,
                "single_mapping": "Shows product profitability.",
                "formula": "gross_profit / revenue",
            }
        },
        "sector_indicators": {},
        "metric_interpretations": [{"metric": "Gross Margin", "interpretation": "Margin is under pressure."}],
        "macro_insights": {"available": False, "summary": "No macro data available."},
        "industry_benchmark_insights": {"available": False, "summary": "No benchmark data available."},
        "analysis_models": {"enabled": ["metric_interpretation"], "skipped": []},
        "weighted_score": {"score": 78.2},
        "industry_comparison": {"available": False, "comparisons": []},
    }

    data = _build_template_data(state)

    assert data["metric_interpretations"][0]["metric"] == "Gross Margin"
    assert data["macro_insights"]["available"] is False
    assert data["industry_benchmark_insights"]["available"] is False
    assert data["analysis_models"]["enabled"] == ["metric_interpretation"]
    assert data["metric_settings_groups"][0]["name"] == "profitability"
    assert data["metric_settings_groups"][0]["indicators"][0]["weight"] == 12
    assert data["metric_blocks"][0]["name"] == "profitability"
    assert data["metric_blocks"][0]["summary"]["available"] == 1
    assert data["metric_blocks"][0]["core_indicators"][0]["name"] == "Gross Margin"


def test_pipeline_exposes_interpretation_state_without_live_wind():
    """Pipeline should attach model-ready interpretation state without spending Wind quota."""
    from nodes.calculate_sector import _test_data_sample
    from web.workflow import run_pipeline

    data = _test_data_sample("宁德时代")
    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "pipeline_no_live_wind"}, clear=False):
        state = run_pipeline(
            company_name="宁德时代",
            stock_code="300750",
            current_period="2025年报",
            primary_business=["动力电池系统", "储能系统"],
            financial_data=data,
        )

    assert "wind_status" in state
    assert state["wind_status"]["live_calls_enabled"] is False
    assert "macro_context" in state
    assert "metric_interpretations" in state
    assert len(state["metric_interpretations"]) > 0
    assert "analysis_models" in state


def test_report_template_contains_collapsible_settings_and_insight_sections():
    """Report UI should expose collapsible settings and narrative insight sections."""
    template = Path("web/templates/report.html").read_text(encoding="utf-8")

    assert "metric_blocks" in template or "metric-block-grid" in template
    assert "macro_insights" in template or "deepseek_macro" in template
    assert "industry_benchmark_insights" in template or "industry_comparison" in template
    assert "{% if has_anomalies %}" in template or "anomaly" in template.lower()


def test_peer_context_uses_bounded_default_codes_and_excludes_current():
    """Peer benchmark context should be bounded and should not benchmark against itself."""
    from web.workflow import _peer_windcodes_for_context

    peers = _peer_windcodes_for_context("002594")

    assert "002594.SZ" not in peers
    assert peers[:2] == ["300750.SZ", "300014.SZ"]


def test_history_followup_and_peer_helpers_are_deterministic():
    """History, follow-up fallback, and peer comparison should work without live APIs."""
    import web.app as web_app

    web_app._REPORT_HISTORY.clear()
    web_app._REPORT_STATES.clear()
    state = {
        "company_name": "TestCo",
        "stock_code": "000001",
        "current_period": "2025",
        "data_completeness": 0.8,
        "weighted_score": {"score": 76.5},
        "risk_summary": {"summary": "Weighted score is 76.5/100. No obvious anomaly signal was detected."},
        "metric_interpretations": [
            {"metric": "ROE", "interpretation": "ROE is 8.2%, based on calculated data."}
        ],
        "anomaly_signals": [],
    }

    from web.shared_state import save_report_state, REPORT_STATES, REPORT_HISTORY
    REPORT_STATES.clear()
    REPORT_HISTORY.clear()
    report_id = save_report_state(state)
    assert report_id in REPORT_STATES
    assert len(REPORT_HISTORY) >= 1

    from web.routes.followup import _fallback_followup_answer
    answer = _fallback_followup_answer(REPORT_STATES[report_id], "请解释 ROE")
    assert "76.5" in answer or "ROE" in answer


def test_new_feature_templates_are_exposed():
    """The uploaded feature spec requires ask, peer compare, and history UI entry points."""
    index = Path("web/templates/index.html").read_text(encoding="utf-8")
    report = Path("web/templates/report.html").read_text(encoding="utf-8")
    compare = Path("web/templates/compare.html").read_text(encoding="utf-8")

    assert 'data-panel="ask"' in index or "ask-panel" in index
    assert 'data-panel="peer"' in index or "peer-panel" in index
    assert 'data-panel="history"' in index or "history-panel" in index
    assert "/compare" in index or "/api/peer-compare" in index
    assert "api/ask" in report or "askReport" in report or "/static/js/report.js" in report
    assert "compare-table" in compare or "peer-compare-table" in compare
    assert "radar-grid" in compare or "radar" in compare.lower()


def test_feature_panels_and_pages_are_navigable():
    """Each documented module should have a usable panel or page, not only a label."""
    import web.app as web_app

    client = web_app.app.test_client()
    home = client.get("/")
    assert home.status_code == 200
    body = home.get_data(as_text=True)
    assert 'data-panel="upload"' in body
    assert 'data-panel="report"' in body
    assert 'data-panel="ask"' in body
    assert 'data-panel="peer"' in body
    assert 'data-panel="history"' in body
    assert 'id="panel-upload"' in body
    assert 'id="panel-ask"' in body
    assert 'id="panel-history"' in body

    assert client.get("/history").status_code == 200
    assert client.get("/ask").status_code == 200


if __name__ == "__main__":
    test_wind_skill_dir_prefers_env_path()
    test_wind_cli_disabled_by_default_in_tests()
    test_wind_cli_uses_cache_before_subprocess()
    test_wind_only_financials_do_not_fallback_to_akshare()
    test_wind_only_macro_context_does_not_fallback_to_akshare()
    test_structured_interpretations_do_not_invent_missing_data()
    test_template_data_exposes_grouped_metric_settings_and_insights()
    test_pipeline_exposes_interpretation_state_without_live_wind()
    test_report_template_contains_collapsible_settings_and_insight_sections()
    test_peer_context_uses_bounded_default_codes_and_excludes_current()
    test_history_followup_and_peer_helpers_are_deterministic()
    test_new_feature_templates_are_exposed()
    test_feature_panels_and_pages_are_navigable()

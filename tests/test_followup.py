"""
Follow-up Q&A logic tests.

Tests the deterministic fallback answer and conversation history handling.
"""
from web.routes.followup import _fallback_followup_answer


def _make_compact_state(**overrides):
    """Build a minimal compact_state dict for testing."""
    base = {
        "company_name": "TestCo",
        "stock_code": "000001",
        "period": "2024",
        "sector": "test",
        "data_completeness": 80,
        "weighted_score": None,
        "risk_summary": {},
        "metric_interpretations": [],
        "industry_benchmark_insights": {},
        "macro_insights": {},
        "anomaly_signals": [],
    }
    base.update(overrides)
    return base


def test_fallback_answer_with_score():
    """Verify fallback includes score when available."""
    state = _make_compact_state(weighted_score={"score": 72})
    answer = _fallback_followup_answer(state, "how is the company?")
    assert "72" in answer
    assert "100" in answer


def test_fallback_answer_with_anomalies():
    """Verify anomaly count mentioned when anomalies exist."""
    state = _make_compact_state(anomaly_signals=[
        {"type": "test", "detail": "anomaly 1"},
        {"type": "test", "detail": "anomaly 2"},
    ])
    answer = _fallback_followup_answer(state, "any problems?")
    assert "2" in answer


def test_fallback_answer_keyword_match():
    """Verify keyword matching pulls relevant metric interpretations."""
    state = _make_compact_state(metric_interpretations=[
        {"metric": "ROE", "interpretation": "ROE is healthy at 15%"},
        {"metric": "current ratio", "interpretation": "Liquidity is tight"},
    ])
    answer = _fallback_followup_answer(state, "what about ROE?")
    assert "ROE" in answer
    assert "15%" in answer

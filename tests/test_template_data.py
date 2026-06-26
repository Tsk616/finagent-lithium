"""
Template data builder tests.

Verifies that build_template_data correctly transforms pipeline state
into template-safe structures for Jinja rendering.
"""
from web.template_data import build_template_data, format_ind_value, format_peer_data


def test_build_template_data_basic():
    """Pass minimal state dict, verify output has required keys."""
    state = {
        "company_name": "TestCo",
        "stock_code": "000001",
        "current_period": "2024",
        "general_indicators": {},
    }
    result = build_template_data(state)
    assert result["company_name"] == "TestCo"
    assert result["stock_code"] == "000001"
    assert result["current_period"] == "2024"
    assert "general_indicators" in result
    assert "report_markdown" in result
    assert "weighted_score" in result
    assert "status" in result


def test_format_ind_value_with_unit():
    """Test value formatting with various units."""
    assert format_ind_value(3.14159, "%") == "3.14%"
    assert format_ind_value(100.5, "亿元") == "100.50亿元"
    assert format_ind_value(0.0, "倍") == "0.00倍"


def test_format_ind_value_no_unit():
    """Test value formatting without unit or with '无' unit."""
    assert format_ind_value(42.0, "") == "42.00"
    assert format_ind_value(42.0, "无") == "42.00"


def test_format_ind_value_none():
    """Test None value returns None."""
    assert format_ind_value(None, "%") is None
    assert format_ind_value(None, "") is None


def test_format_peer_data_empty():
    """Test empty peer list returns empty."""
    assert format_peer_data([]) == []
    assert format_peer_data(None) == []

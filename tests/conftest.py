"""
Shared test fixtures for FinAgent-Lithium.

Provides mock financial data, Flask test client, and common assertions.
"""
import sys
from pathlib import Path

import pytest

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def app():
    """Create Flask test application."""
    from web.app import app
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()


@pytest.fixture
def sample_financial_data():
    """Standard mock financial data for testing."""
    return {
        "营业收入": 360000000000,
        "营业成本": 290000000000,
        "净利润": 44000000000,
        "扣非净利润": 40000000000,
        "总资产": 650000000000,
        "总负债": 400000000000,
        "流动资产": 320000000000,
        "流动负债": 280000000000,
        "存货": 50000000000,
        "预付款项": 5000000000,
        "经营活动现金流净额": 55000000000,
        "净资产": 250000000000,
        "所有者权益": 250000000000,
        "销售费用": 8000000000,
        "管理费用": 12000000000,
        "财务费用": 3000000000,
        "研发费用": 18000000000,
    }

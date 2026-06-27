"""Tests for MX Data (东方财富妙想) adapter integration."""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


SAMPLE_MX_RESPONSE = {
    "success": True,
    "status": 0,
    "code": 0,
    "message": "ok",
    "data": {
        "message": "OK",
        "status": 0,
        "code": 0,
        "data": {
            "searchDataResultDTO": {
                "questionId": "test-id",
                "dataTableDTOList": [
                    {
                        "code": "300750.SZ",
                        "entityName": "2024年报",
                        "rawTable": {
                            "f1": ["362012554000", "390925396311"],
                            "f2": ["273518959000", "-"],
                            "f3": ["44992919000", "-"],
                            "f4": ["786658123000", "-"],
                            "f5": ["513201949000", "-"],
                            "f6": ["273456174000", "295296286337"],
                            "f7": ["510142089000", "-"],
                            "f8": ["317171534000", "-"],
                            "f9": ["59835533000", "-"],
                            "f10": ["96990345000", "-"],
                            "f11": ["3562797000", "-"],
                            "f12": ["5165670000", "-"],
                            "f13": ["9689839000", "-"],
                            "f14": ["18606756000", "-"],
                            "f15": ["5969685000", "-"],
                            "f16": ["66776402000", "-"],
                            "headName": [
                                "宁德时代(300750.SZ)",
                                "宁德时代(03750.HK)(最新年结日12-31)",
                            ],
                        },
                        "nameMap": {
                            "f1": "营业收入",
                            "f2": "营业成本",
                            "f3": "扣除非经常性损益后归属于母公司股东的净利润",
                            "f4": "资产总计",
                            "f5": "负债合计",
                            "f6": "股东权益合计",
                            "f7": "流动资产合计",
                            "f8": "流动负债合计",
                            "f9": "存货",
                            "f10": "经营活动产生的现金流量净额",
                            "f11": "销售费用",
                            "f12": "管理费用",
                            "f13": "财务费用",
                            "f14": "研发费用",
                            "f15": "预付款项",
                            "f16": "应收账款及票据",
                            "securityName": "股票名称",
                            "headNameSub": "数据来源",
                        },
                    }
                ],
            }
        },
    },
}


def test_mx_parse_financials_extracts_a_share_data():
    """MX parser extracts A-share data using rawTable and nameMap."""
    from nodes.wind_adapter import _mx_parse_financials

    parsed = _mx_parse_financials(SAMPLE_MX_RESPONSE)

    assert parsed["营业收入"] == 362012554000.0
    assert parsed["营业成本"] == 273518959000.0
    assert parsed["扣非净利润"] == 44992919000.0
    assert parsed["总资产"] == 786658123000.0
    assert parsed["总负债"] == 513201949000.0
    assert parsed["净资产"] == 273456174000.0
    assert parsed["流动资产"] == 510142089000.0
    assert parsed["流动负债"] == 317171534000.0
    assert parsed["存货"] == 59835533000.0
    assert parsed["经营活动现金流净额"] == 96990345000.0
    assert parsed["销售费用"] == 3562797000.0
    assert parsed["管理费用"] == 5165670000.0
    assert parsed["财务费用"] == 9689839000.0
    assert parsed["研发费用"] == 18606756000.0
    assert parsed["预付款项"] == 5969685000.0
    assert parsed["应收账款"] == 66776402000.0


def test_mx_parse_skips_hk_only_values():
    """MX parser skips values that only exist for H-share."""
    from nodes.wind_adapter import _mx_parse_financials

    response = {
        "status": 0,
        "data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [{
            "rawTable": {
                "f1": ["-", "100000"],
                "headName": ["宁德时代(300750.SZ)", "宁德时代(03750.HK)"],
            },
            "nameMap": {"f1": "营业收入"},
        }]}}},
    }
    parsed = _mx_parse_financials(response)
    assert "营业收入" not in parsed


def test_mx_parse_with_rename_map():
    """MX parser applies rename_map for prior period mapping."""
    from nodes.wind_adapter import _mx_parse_financials

    response = {
        "status": 0,
        "data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [{
            "rawTable": {
                "f1": ["400917044900"],
                "f2": ["45433890100"],
                "headName": ["宁德时代(300750.SZ)"],
            },
            "nameMap": {"f1": "营业收入", "f2": "存货"},
        }]}}},
    }
    rename = {"营业收入": "上期营业收入", "存货": "期初存货"}
    parsed = _mx_parse_financials(response, rename_map=rename)
    assert parsed["上期营业收入"] == 400917044900.0
    assert parsed["期初存货"] == 45433890100.0


def test_mx_query_returns_none_without_apikey():
    """MX query gracefully returns None when MX_APIKEY is not set."""
    from nodes.wind_adapter import _mx_query

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MX_APIKEY", None)
        result = _mx_query("test query")
    assert result is None


def test_mx_fetch_financials_returns_none_without_apikey():
    """Full MX fetch returns None when API key is missing."""
    from nodes.wind_adapter import _mx_fetch_financials

    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MX_APIKEY", None)
        result = _mx_fetch_financials("300750.SZ", "2024年报")
    assert result is None


def test_mx_supplement_fills_gaps():
    """MX supplement fills missing accounts without overwriting existing ones."""
    from nodes.wind_adapter import _mx_supplement, _mx_query

    existing = {"营业收入": 100.0, "营业成本": None, "扣非净利润": None}

    mock_response = {
        "status": 0,
        "data": {"data": {"searchDataResultDTO": {"dataTableDTOList": [{
            "rawTable": {
                "f1": ["200.0"],
                "f2": ["50.0"],
                "f3": ["30.0"],
                "headName": ["宁德时代(300750.SZ)"],
            },
            "nameMap": {"f1": "营业收入", "f2": "营业成本", "f3": "扣除非经常性损益后归属于母公司股东的净利润"},
        }]}}},
    }

    with patch("nodes.wind_adapter._mx_query", return_value=mock_response):
        _mx_supplement(existing, "300750.SZ", "2024年报")

    assert existing["营业收入"] == 100.0
    assert existing["营业成本"] == 50.0
    assert existing["扣非净利润"] == 30.0


def test_mx_api_timeout_does_not_crash():
    """MX API timeout is handled gracefully in fetch_financials."""
    from nodes.wind_adapter import _mx_query

    with patch.dict(os.environ, {"MX_APIKEY": "test_key"}):
        with patch("requests.post", side_effect=Exception("timeout")):
            result = _mx_query("test")
    assert result is None


def test_mx_period_label_conversion():
    """Period label conversion handles various input formats."""
    from nodes.wind_adapter import _mx_period_label, _mx_prior_period_label

    assert _mx_period_label("2024年报") == "2024年报"
    assert _mx_period_label("2024三季报") == "2024三季报"
    assert _mx_prior_period_label("2024年报") == "2023年报"
    assert _mx_prior_period_label("2025三季报") == "2024年报"


def test_mx_parse_handles_empty_response():
    """MX parser returns empty dict for malformed responses."""
    from nodes.wind_adapter import _mx_parse_financials

    assert _mx_parse_financials({}) == {}
    assert _mx_parse_financials({"data": None}) == {}
    assert _mx_parse_financials({"data": {"data": {"searchDataResultDTO": {"dataTableDTOList": []}}}}) == {}


def test_fetch_financials_tries_mx_when_wind_fails():
    """fetch_financials falls through to MX when Wind fails."""
    from nodes.wind_adapter import fetch_financials

    with patch("nodes.wind_adapter._wind_fetch_financials", return_value=None), \
         patch("nodes.wind_adapter._mx_fetch_financials", return_value={"营业收入": 100.0, "营业成本": 50.0, "扣非净利润": 10.0}) as mx_mock, \
         patch("nodes.wind_adapter._mx_supplement"), \
         patch("nodes.wind_adapter._mx_fetch_prior_and_merge"):
        result = fetch_financials("300750.SZ", "2024年报")

    mx_mock.assert_called_once()
    assert result is not None
    assert result["营业收入"] == 100.0

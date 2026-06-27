"""Tests for historical data analytics (CAGR, inflection detection)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nodes.historical_data import (
    compute_cagr,
    detect_inflection_points,
    _derive_period_labels,
    _derive_ratios,
)


def test_derive_period_labels_normal():
    labels = _derive_period_labels("2024年报", years=5)
    assert labels == ["2020年报", "2021年报", "2022年报", "2023年报", "2024年报"]


def test_derive_period_labels_3_years():
    labels = _derive_period_labels("2025年报", years=3)
    assert labels == ["2023年报", "2024年报", "2025年报"]


def test_derive_period_labels_invalid():
    assert _derive_period_labels("") == []
    assert _derive_period_labels("最新一期") == []


def test_cagr_basic():
    result = compute_cagr(
        [100.0, 120.0, 144.0, 172.8],
        ["2020年报", "2021年报", "2022年报", "2023年报"],
    )
    assert result is not None
    assert result["years"] == 3
    assert abs(result["value"] - 20.0) < 0.5


def test_cagr_with_none_values():
    result = compute_cagr(
        [100.0, None, None, 200.0],
        ["2020年报", "2021年报", "2022年报", "2023年报"],
    )
    assert result is not None
    assert result["years"] == 3


def test_cagr_insufficient_data():
    assert compute_cagr([100.0], ["2020年报"]) is None
    assert compute_cagr([None, None], ["2020年报", "2021年报"]) is None


def test_cagr_negative_values():
    assert compute_cagr([-100.0, 200.0], ["2020年报", "2021年报"]) is None


def test_inflection_sign_reversal():
    points = detect_inflection_points(
        "营业收入",
        [100, 120, 140, 130],
        ["2020年报", "2021年报", "2022年报", "2023年报"],
    )
    assert len(points) >= 1
    reversal = [p for p in points if p["type"] == "sign_reversal"]
    assert len(reversal) == 1
    assert reversal[0]["period"] == "2023年报"


def test_inflection_recovery():
    points = detect_inflection_points(
        "净利润",
        [100, 80, 70, 85],
        ["2020年报", "2021年报", "2022年报", "2023年报"],
    )
    recovery = [p for p in points if p["type"] == "recovery"]
    assert len(recovery) == 1


def test_inflection_no_change():
    points = detect_inflection_points(
        "营业收入",
        [100, 110, 121, 133],
        ["2020年报", "2021年报", "2022年报", "2023年报"],
    )
    assert len(points) == 0


def test_inflection_with_none():
    points = detect_inflection_points(
        "营业收入",
        [100, None, 140, 130],
        ["2020年报", "2021年报", "2022年报", "2023年报"],
    )
    assert isinstance(points, list)


def test_derive_ratios():
    metrics = {
        "营业收入": [100.0, 200.0],
        "营业成本": [70.0, 130.0],
        "净利润": [10.0, 25.0],
        "总资产": [500.0, 800.0],
        "总负债": [300.0, 500.0],
        "净资产": [200.0, 300.0],
    }
    ratios = _derive_ratios(metrics, 2)
    assert ratios["销售毛利率"][0] == 30.0
    assert ratios["销售毛利率"][1] == 35.0
    assert ratios["资产负债率"][0] == 60.0
    assert ratios["净利率"][0] == 10.0
    assert ratios["ROE"][1] == round(25.0 / 300.0 * 100, 2)


def test_derive_ratios_with_none():
    metrics = {
        "营业收入": [100.0, None],
        "营业成本": [70.0, None],
        "净利润": [None, None],
        "总资产": [500.0, 800.0],
        "总负债": [300.0, 500.0],
        "净资产": [200.0, 300.0],
    }
    ratios = _derive_ratios(metrics, 2)
    assert ratios["销售毛利率"][0] == 30.0
    assert ratios["销售毛利率"][1] is None
    assert ratios["净利率"][0] is None


if __name__ == "__main__":
    test_derive_period_labels_normal()
    test_derive_period_labels_3_years()
    test_derive_period_labels_invalid()
    test_cagr_basic()
    test_cagr_with_none_values()
    test_cagr_insufficient_data()
    test_cagr_negative_values()
    test_inflection_sign_reversal()
    test_inflection_recovery()
    test_inflection_no_change()
    test_inflection_with_none()
    test_derive_ratios()
    test_derive_ratios_with_none()
    print("All historical_data tests passed!")

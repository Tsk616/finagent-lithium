"""
Six-dimension financial trend-prediction engine for FinAgent-Lithium.

Forecasts next-year ability scores (profitability / operation / growth /
solvency / cash / composite) from 3-5 years of history using trend
extrapolation + lithium industry-cycle correction + Monte-Carlo intervals.

Numeric forecasting is pure Python/numpy (deterministic). An optional LLM pass
adds scenario narrative only. Track weights are reused from
metric_weight_config.json (via metric_config.load_metric_config) rather than a
duplicate library — single source of truth.

Ported/adapted from the user's 锂电行业财务六维预测模型_v2.0.py spec.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

import numpy as np


# ── Ability dimensions (map to indicator categories) ──────────────────
ABILITY_DIMENSIONS = {
    "盈利能力": {"source_types": ["盈利能力"]},
    "运营能力": {"source_types": ["营运能力"]},
    "成长能力": {"source_types": ["发展能力"]},
    "偿债能力": {"source_types": ["短期偿债能力", "长期偿债能力", "偿债能力"]},
    "现金能力": {"source_types": ["盈利质量"]},
}
_DIMENSION_ORDER = ["盈利能力", "运营能力", "成长能力", "偿债能力", "现金能力"]


# ── Per-indicator forecast method + cycle sensitivity ─────────────────
INDICATOR_PREDICTION_METHODS = {
    "销售毛利率": {"method": "trend_cycle", "cycle_sensitive": True},
    "扣非销售净利率": {"method": "trend_cycle", "cycle_sensitive": True},
    "期间费用率": {"method": "trend_mean_reversion", "cycle_sensitive": False},
    "扣非ROE": {"method": "trend_cycle", "cycle_sensitive": True},
    "加工费毛利率": {"method": "trend_mean_reversion", "cycle_sensitive": False},
    "研发费用率": {"method": "linear_trend", "cycle_sensitive": False},
    "营收同比增速": {"method": "trend_cycle", "cycle_sensitive": True},
    "应收账款同比增速": {"method": "trend_cycle", "cycle_sensitive": True},
    "存货同比增速": {"method": "trend_cycle", "cycle_sensitive": True},
    "存货周转率": {"method": "moving_average", "cycle_sensitive": True},
    "存货周转天数": {"method": "moving_average", "cycle_sensitive": True},
    "应收账款周转率": {"method": "moving_average", "cycle_sensitive": False},
    "固定资产周转率": {"method": "linear_trend", "cycle_sensitive": True},
    "资产负债率": {"method": "trend_mean_reversion", "cycle_sensitive": False},
    "速动比率": {"method": "trend_mean_reversion", "cycle_sensitive": False},
    "流动比率": {"method": "trend_mean_reversion", "cycle_sensitive": False},
    "净利润现金含量": {"method": "moving_average", "cycle_sensitive": True},
    "存货跌价损失率": {"method": "trend_cycle", "cycle_sensitive": True},
}


# ── Lithium industry cycle index (static; TODO: wire live lithium trend) ──
LITHIUM_CYCLE_INDEX = {
    "2021": {"index": 85, "phase": "上行期"},
    "2022": {"index": 95, "phase": "顶部"},
    "2023": {"index": 70, "phase": "下行期"},
    "2024": {"index": 45, "phase": "底部"},
    "2025": {"index": 55, "phase": "复苏期"},
    "2026": {"index": 65, "phase": "上行期"},
    "2027": {"index": 70, "phase": "上行期"},
    "2028": {"index": 72, "phase": "上行期"},
}

CYCLE_ADJUSTMENT_FACTORS = {
    "销售毛利率": {"上行期": 1.08, "下行期": 0.92, "底部": 0.95, "顶部": 1.02, "复苏期": 1.05},
    "扣非销售净利率": {"上行期": 1.12, "下行期": 0.88, "底部": 0.90, "顶部": 1.03, "复苏期": 1.08},
    "扣非ROE": {"上行期": 1.10, "下行期": 0.90, "底部": 0.92, "顶部": 1.02, "复苏期": 1.06},
    "存货跌价损失率": {"上行期": 0.85, "下行期": 1.20, "底部": 1.30, "顶部": 0.80, "复苏期": 1.10},
    "营收同比增速": {"上行期": 1.15, "下行期": 0.85, "底部": 0.90, "顶部": 1.05, "复苏期": 1.12},
    "存货同比增速": {"上行期": 1.10, "下行期": 0.90, "底部": 0.85, "顶部": 1.05, "复苏期": 1.00},
    "应收账款同比增速": {"上行期": 1.08, "下行期": 0.90, "底部": 0.90, "顶部": 1.03, "复苏期": 1.05},
    "净利润现金含量": {"上行期": 1.05, "下行期": 0.95, "底部": 0.92, "顶部": 1.02, "复苏期": 1.03},
    "固定资产周转率": {"上行期": 1.05, "下行期": 0.95, "底部": 0.93, "顶部": 1.02, "复苏期": 1.04},
    "存货周转率": {"上行期": 1.06, "下行期": 0.94, "底部": 0.92, "顶部": 1.03, "复苏期": 1.04},
    "存货周转天数": {"上行期": 0.95, "下行期": 1.05, "底部": 1.08, "顶部": 0.95, "复苏期": 1.02},
}


# ── Score thresholds (功效系数法) from the AI prompt STEP 3 ──────────────
# Anchor points (value, score 0-100). Interpolated; direction encoded by the
# slope of the anchors (ascending score = higher-better; descending = lower).
SCORE_ANCHORS = {
    "销售毛利率": [(0, 20), (5, 40), (12, 60), (20, 80), (30, 100)],
    "扣非销售净利率": [(-10, 20), (-5, 40), (0, 55), (4, 75), (8, 100)],
    "扣非ROE": [(-10, 20), (-5, 40), (2, 60), (6, 80), (12, 100)],
    "加工费毛利率": [(0, 20), (5, 45), (12, 65), (20, 85), (28, 100)],
    "存货周转率": [(0.2, 20), (0.5, 40), (1.5, 60), (3, 80), (5, 100)],
    "存货周转天数": [(30, 100), (90, 80), (150, 60), (240, 40), (360, 20)],
    "固定资产周转率": [(0.1, 20), (0.2, 40), (0.4, 60), (0.8, 80), (1.5, 100)],
    "应收账款周转率": [(0.5, 20), (1, 40), (2, 60), (4, 80), (8, 100)],
    "营收同比增速": [(-40, 20), (-20, 40), (0, 60), (10, 80), (25, 100)],
    "应收账款同比增速": [(60, 30), (30, 60), (15, 80), (5, 95), (0, 100)],
    "存货同比增速": [(60, 30), (30, 60), (15, 80), (5, 95), (0, 100)],
    "研发费用率": [(0.2, 20), (0.5, 40), (1.5, 60), (3.5, 80), (6, 100)],
    "期间费用率": [(30, 20), (20, 45), (12, 65), (6, 85), (2, 100)],
    "资产负债率": [(45, 100), (60, 80), (75, 60), (90, 40), (100, 20)],
    "速动比率": [(0.2, 20), (0.3, 40), (0.5, 60), (0.7, 80), (1.0, 100)],
    "流动比率": [(0.5, 20), (0.6, 40), (0.9, 60), (1.2, 80), (1.5, 100)],
    "净利润现金含量": [(0, 20), (10, 40), (40, 60), (70, 80), (100, 100)],
    "存货跌价损失率": [(1, 100), (3, 80), (8, 60), (20, 40), (30, 20)],
}


# ── Indicator type classification (for ability mapping) ───────────────
_TYPE_LISTS = {
    "盈利能力": ["销售毛利率", "扣非销售净利率", "期间费用率", "扣非ROE", "加工费毛利率"],
    "营运能力": ["存货周转率", "存货周转天数", "应收账款周转率", "固定资产周转率"],
    "发展能力": ["营收同比增速", "应收账款同比增速", "存货同比增速", "研发费用率"],
    "偿债能力": ["资产负债率", "速动比率", "流动比率"],
    "盈利质量": ["净利润现金含量", "存货跌价损失率"],
}


def _norm(name: str) -> str:
    """Drop whitespace so '扣非 ROE' and '扣非ROE' match."""
    return re.sub(r"\s+", "", str(name or ""))


_METHODS_NORM = {_norm(k): v for k, v in INDICATOR_PREDICTION_METHODS.items()}
_CYCLE_NORM = {_norm(k): v for k, v in CYCLE_ADJUSTMENT_FACTORS.items()}
_ANCHORS_NORM = {_norm(k): v for k, v in SCORE_ANCHORS.items()}
_TYPE_BY_NAME = {_norm(n): t for t, names in _TYPE_LISTS.items() for n in names}


class PredictionEngine:
    """Trend + cycle + Monte-Carlo forecaster over indicator time series."""

    def __init__(self, track_weights: Dict[str, Dict], cycle_year: str = "2026"):
        self.track_weights = track_weights
        self.cycle_year = str(cycle_year)
        self.cycle_info = LITHIUM_CYCLE_INDEX.get(self.cycle_year, {"index": 60, "phase": "复苏期"})

    # ── base forecast methods ──
    def _linear_trend_predict(self, values: List[float]) -> float:
        n = len(values)
        if n < 2:
            return float(values[-1]) if values else 0.0
        x = np.arange(n)
        slope, intercept = np.polyfit(x, values, 1)
        return float(slope * n + intercept)

    def _moving_average_predict(self, values: List[float], window: int = 3) -> float:
        n = len(values)
        if n < 2:
            return float(values[-1]) if values else 0.0
        recent = values[-window:] if n >= window else values
        ma = float(np.mean(recent))
        return ma + (values[-1] - values[-2]) * 0.5

    def _mean_reversion_predict(self, values: List[float]) -> float:
        if not values:
            return 0.0
        hist_mean = float(np.mean(values))
        last = values[-1]
        revert = 1 / len(values) if values else 0.3
        pred = last + (hist_mean - last) * revert
        if len(values) >= 3:
            pred += float(np.mean(np.diff(values[-3:]))) * 0.3
        elif len(values) == 2:
            pred += (values[-1] - values[-2]) * 0.3
        return float(pred)

    def _trend_cycle_predict(self, values: List[float]) -> float:
        return self._linear_trend_predict(values)

    def _predict_indicator(self, name: str, values: List[float]) -> Dict[str, Any]:
        if not values or len(values) < 2:
            last = float(values[-1]) if values else 0.0
            return {
                "base_prediction": last, "cycle_adjusted": last,
                "optimistic": last * 1.1, "pessimistic": last * 0.9,
                "confidence_interval": [last, last], "method_used": "insufficient_data",
            }

        cfg = _METHODS_NORM.get(_norm(name), {"method": "linear_trend", "cycle_sensitive": False})
        method = cfg["method"]
        if method == "moving_average":
            base = self._moving_average_predict(values)
        elif method == "trend_mean_reversion":
            base = self._mean_reversion_predict(values)
        elif method == "trend_cycle":
            base = self._trend_cycle_predict(values)
        else:
            base = self._linear_trend_predict(values)

        cycle_adjusted = base
        cyc = _CYCLE_NORM.get(_norm(name))
        if cyc and cfg.get("cycle_sensitive"):
            factor = cyc.get(self.cycle_info.get("phase", "复苏期"), 1.0)
            cycle_adjusted = base * factor

        diffs = np.diff(values)
        volatility = float(np.std(diffs)) if len(diffs) > 1 else (abs(diffs[0]) * 0.5 if len(diffs) == 1 else 0.0)
        if volatility < abs(cycle_adjusted) * 0.02:
            volatility = abs(cycle_adjusted) * 0.05

        return {
            "base_prediction": round(base, 4),
            "cycle_adjusted": round(cycle_adjusted, 4),
            "optimistic": round(cycle_adjusted + volatility * 1.5, 4),
            "pessimistic": round(cycle_adjusted - volatility * 1.5, 4),
            "confidence_interval": [round(cycle_adjusted - volatility * 2, 4),
                                    round(cycle_adjusted + volatility * 2, 4)],
            "method_used": method,
            "volatility": round(volatility, 4),
        }

    def _value_to_score(self, name: str, value: Optional[float]) -> float:
        anchors = _ANCHORS_NORM.get(_norm(name))
        if not anchors or value is None:
            return 60.0
        xs = [a[0] for a in anchors]
        ys = [a[1] for a in anchors]
        if value <= xs[0]:
            return float(ys[0])
        if value >= xs[-1]:
            return float(ys[-1])
        for i in range(1, len(xs)):
            if value <= xs[i]:
                x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
                t = (value - x0) / (x1 - x0) if x1 != x0 else 0
                return float(y0 + (y1 - y0) * t)
        return 60.0

    def _indicator_type(self, name: str) -> Optional[str]:
        return _TYPE_BY_NAME.get(_norm(name))

    def predict_abilities(self, historical: Dict[str, List[float]],
                          scenario: str = "baseline") -> Dict[str, Dict]:
        # Forecast each fed indicator once.
        preds = {}
        for name, vals in historical.items():
            p = self._predict_indicator(name, vals)
            value = {"optimistic": p["optimistic"], "pessimistic": p["pessimistic"]}.get(
                scenario, p["cycle_adjusted"])
            preds[name] = {"value": value, "meta": p}

        results: Dict[str, Dict] = {}
        for ability, cfg in ABILITY_DIMENSIONS.items():
            score_sum = 0.0
            weight_sum = 0.0
            details = []
            for name, w in self.track_weights.items():
                if self._indicator_type(name) not in cfg["source_types"]:
                    continue
                weight = float(w.get("weight", 0))
                if name in preds:
                    pv = preds[name]["value"]
                    raw = self._value_to_score(name, pv)
                    score_sum += raw * weight
                    weight_sum += weight
                    details.append({
                        "indicator": name,
                        "historical_values": [round(v, 2) for v in historical.get(name, [])],
                        "predicted_value": round(pv, 2),
                        "raw_score": round(raw, 1),
                        "weight": weight,
                        "confidence_interval": preds[name]["meta"]["confidence_interval"],
                        "method": preds[name]["meta"]["method_used"],
                        "category": w.get("category", "通用"),
                    })
            score = round(score_sum / weight_sum, 2) if weight_sum > 0 else 60.0
            results[ability] = {"predicted_score": score, "weight_sum": round(weight_sum, 2),
                                "details": details}

        # Composite = weighted average of the five dims by their weight_sum.
        tw = sum(r["weight_sum"] for r in results.values())
        comp = (sum(r["predicted_score"] * r["weight_sum"] for r in results.values()) / tw) if tw > 0 else 60.0
        results["综合财务能力"] = {"predicted_score": round(comp, 2), "weight_sum": round(tw, 2), "details": []}
        return results

    def monte_carlo_simulation(self, historical: Dict[str, List[float]],
                               n_simulations: int = 1000) -> Dict[str, Dict]:
        # Precompute CIs and ability weight sums once.
        cis = {name: self._predict_indicator(name, vals)["confidence_interval"]
               for name, vals in historical.items()}
        base = self.predict_abilities(historical, "baseline")
        weight_sums = {a: base[a]["weight_sum"] for a in _DIMENSION_ORDER}

        sims = {a: [] for a in _DIMENSION_ORDER}
        comp_sims = []
        rng = np.random.default_rng(42)  # deterministic for reproducible reports
        for _ in range(n_simulations):
            sample = {name: [float(rng.uniform(ci[0], ci[1]))] for name, ci in cis.items()}
            r = self.predict_abilities(sample, "baseline")
            for a in _DIMENSION_ORDER:
                sims[a].append(r[a]["predicted_score"])
            tw = sum(weight_sums.values())
            comp = (sum(r[a]["predicted_score"] * weight_sums[a] for a in _DIMENSION_ORDER) / tw) if tw > 0 else 60.0
            comp_sims.append(comp)

        out = {a: _distribution(sims[a]) for a in _DIMENSION_ORDER}
        out["综合财务能力"] = _distribution(comp_sims)
        return out


def _distribution(scores: List[float]) -> Dict[str, Any]:
    arr = np.array(scores) if scores else np.array([60.0])
    return {
        "mean": round(float(np.mean(arr)), 2),
        "std": round(float(np.std(arr)), 2),
        "median": round(float(np.median(arr)), 2),
        "percentile_5": round(float(np.percentile(arr, 5)), 2),
        "percentile_95": round(float(np.percentile(arr, 95)), 2),
        "probability_above_80": round(float(np.mean(arr >= 80)) * 100, 1),
        "probability_above_60": round(float(np.mean(arr >= 60)) * 100, 1),
        "probability_below_40": round(float(np.mean(arr < 40)) * 100, 1),
    }


# ── Historical input adapter ──────────────────────────────────────────

def _yoy_series(values: List[Optional[float]]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(1, len(values)):
        prev, cur = values[i - 1], values[i]
        if prev is not None and cur is not None and prev != 0:
            out.append(round((cur - prev) / abs(prev) * 100, 2))
        else:
            out.append(None)
    return out


def _clean(values: Optional[List[Optional[float]]]) -> List[float]:
    """Keep non-None values; require >=2 to be forecastable."""
    if not values:
        return []
    vals = [float(v) for v in values if v is not None]
    return vals if len(vals) >= 2 else []


def _build_prediction_input(historical: Dict[str, Any]) -> Dict[str, List[float]]:
    """Build {indicator: [yearly series]} from historical_data output."""
    metrics = historical.get("metrics") or {}
    dr = historical.get("derived_ratios") or {}
    out: Dict[str, List[float]] = {}

    # Direct ratio series (existing + Unit B expansions), mapped to engine names.
    direct = {
        "销售毛利率": dr.get("销售毛利率"),
        "资产负债率": dr.get("资产负债率"),
        "扣非销售净利率": dr.get("净利率"),       # net margin as proxy
        "扣非ROE": dr.get("ROE"),                 # ROE as proxy
        "流动比率": dr.get("流动比率"),
        "速动比率": dr.get("速动比率"),
        "存货周转率": dr.get("存货周转率"),
        "应收账款周转率": dr.get("应收账款周转率"),
        "固定资产周转率": dr.get("固定资产周转率"),
    }
    for name, series in direct.items():
        cleaned = _clean(series)
        if cleaned:
            out[name] = cleaned

    # Derived from raw metric series.
    revenue = metrics.get("营业收入") or []
    growth = _clean(_yoy_series(revenue))
    if growth:
        out["营收同比增速"] = growth

    rnd = metrics.get("研发费用") or []
    rd_ratio = _clean([
        round(rnd[i] / revenue[i] * 100, 2)
        if i < len(rnd) and i < len(revenue) and rnd[i] is not None and revenue[i] else None
        for i in range(max(len(rnd), len(revenue)))
    ])
    if rd_ratio:
        out["研发费用率"] = rd_ratio

    ocf = metrics.get("经营活动现金流净额") or []
    profit = metrics.get("净利润") or []
    cash_q = _clean([
        round(ocf[i] / profit[i] * 100, 2)
        if i < len(ocf) and i < len(profit) and ocf[i] is not None and profit[i] else None
        for i in range(max(len(ocf), len(profit)))
    ])
    if cash_q:
        out["净利润现金含量"] = cash_q

    return out


def _track_weights_from_config(sector_code: Optional[str]) -> Dict[str, Dict]:
    """Adapt metric_weight_config sector metrics into engine track_weights."""
    try:
        from nodes.metric_config import load_metric_config
        cfg = load_metric_config()
    except Exception:
        return {}
    sector = (cfg.get("sectors") or {}).get(sector_code or "")
    if not sector:
        return {}
    tw: Dict[str, Dict] = {}
    for m in sector.get("metrics", []):
        name = m.get("canonical_name") or m.get("display_name")
        if not name:
            continue
        tw[name] = {"weight": float(m.get("weight", 0)),
                    "category": m.get("metric_category") or m.get("category") or "通用"}
    return tw


def _next_year(current_period: str, periods: List[str]) -> int:
    for src in [current_period] + list(reversed(periods or [])):
        m = re.match(r"(\d{4})", src or "")
        if m:
            return int(m.group(1)) + 1
    return 2027


def _assemble(baseline, optimistic, pessimistic, mc) -> Dict[str, Dict]:
    abilities = {}
    for dim in _DIMENSION_ORDER + ["综合财务能力"]:
        b, o, p = baseline[dim], optimistic[dim], pessimistic[dim]
        d = mc[dim]
        abilities[dim] = {
            "baseline": b["predicted_score"],
            "optimistic": o["predicted_score"],
            "pessimistic": p["predicted_score"],
            "ci_low": d["percentile_5"],
            "ci_high": d["percentile_95"],
            "mc_mean": d["mean"],
            "mc_std": d["std"],
            "prob_above_80": d["probability_above_80"],
            "prob_above_60": d["probability_above_60"],
            "prob_below_40": d["probability_below_40"],
        }
    return abilities


def _assemble_table(baseline: Dict[str, Dict]) -> List[Dict[str, Any]]:
    table = []
    for dim in _DIMENSION_ORDER:
        for det in baseline[dim]["details"]:
            table.append({"ability": dim, **det})
    return table


def _weight_split(track_weights: Dict[str, Dict]) -> Dict[str, float]:
    total = sum(float(w.get("weight", 0)) for w in track_weights.values()) or 1.0
    general = sum(float(w.get("weight", 0)) for w in track_weights.values()
                  if (w.get("category") or "通用") == "通用")
    return {"general_pct": round(general / total * 100, 1),
            "special_pct": round((total - general) / total * 100, 1)}


def _llm_scenario_narrative(result: Dict[str, Any], company: str, sector: str,
                            cycle_phase: str, llm_config: Optional[Dict]) -> Dict[str, Any]:
    """Optional LLM scenario interpretation, grounded in the numeric forecast."""
    if not llm_config or not llm_config.get("api_key"):
        return {}
    try:
        from nodes.llm_client import call_llm
        comp = result["abilities"]["综合财务能力"]
        facts = {
            "公司": company, "赛道": sector, "目标年度": result.get("target_year"),
            "行业周期": cycle_phase,
            "综合财务能力": {"基准": comp["baseline"], "乐观": comp["optimistic"],
                       "悲观": comp["pessimistic"],
                       "P(≥80)": comp["prob_above_80"], "P(≥60)": comp["prob_above_60"],
                       "P(<40)": comp["prob_below_40"]},
            "六维基准得分": {d: result["abilities"][d]["baseline"] for d in _DIMENSION_ORDER},
        }
        prompt = (
            "你是锂电行业财务预测分析师。下面是对某公司未来一年六大财务能力的"
            "量化预测结果（已用趋势外推+周期修正+蒙特卡洛算出，数字是既定事实，不要改动）。"
            "请基于这些数字给出情景解读。\n\n"
            "## 要求\n只输出 JSON，格式：\n"
            "{\n"
            '  "scenario_summary": "一段80-120字综合解读",\n'
            '  "baseline": "基准情景下的经营判断（50字内）",\n'
            '  "optimistic": "乐观情景触发条件与表现（50字内）",\n'
            '  "pessimistic": "悲观情景触发条件与风险（50字内）",\n'
            '  "key_risks": ["风险1", "风险2"],\n'
            '  "tracking_suggestions": ["跟踪建议1", "跟踪建议2"]\n'
            "}\n## 约束\n只基于给定数字推理，不编造；只输出 JSON 不加 markdown。"
        )
        resp = call_llm(prompt, json.dumps(facts, ensure_ascii=False), config={"timeout": 20})
        if not resp or len(resp.strip()) < 10:
            return {}
        cleaned = resp.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(cleaned)
    except Exception:
        return {}


def run_prediction_analysis(state: Dict[str, Any],
                            llm_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Top-level: build input, forecast six abilities, assemble report dict."""
    historical = state.get("historical_data") or {}
    series = _build_prediction_input(historical)
    history_years = max((len(v) for v in series.values()), default=0)
    if history_years < 2 or not series:
        return {"available": False, "reason": "历史数据不足（需至少 2 年可比序列）"}

    track_weights = _track_weights_from_config(state.get("_sector_code"))
    if not track_weights:
        track_weights = {k: {"weight": 5, "category": "通用"} for k in series}

    periods = historical.get("periods") or []
    target_year = _next_year(state.get("current_period", ""), periods)
    cycle_year = str(target_year)
    cycle_phase = LITHIUM_CYCLE_INDEX.get(cycle_year, {"phase": "复苏期"})["phase"]

    eng = PredictionEngine(track_weights, cycle_year=cycle_year)
    baseline = eng.predict_abilities(series, "baseline")
    optimistic = eng.predict_abilities(series, "optimistic")
    pessimistic = eng.predict_abilities(series, "pessimistic")
    mc = eng.monte_carlo_simulation(series, n_simulations=1000)

    result = {
        "available": True,
        "target_year": target_year,
        "cycle_phase": cycle_phase,
        "history_years": history_years,
        "weight_split": _weight_split(track_weights),
        "abilities": _assemble(baseline, optimistic, pessimistic, mc),
        "indicator_table": _assemble_table(baseline),
        "narrative": {},
    }

    sector_name = state.get("sector_level2") or state.get("sector_level1") or "锂电"
    result["narrative"] = _llm_scenario_narrative(
        result, state.get("company_name", ""), sector_name, cycle_phase, llm_config)
    return result

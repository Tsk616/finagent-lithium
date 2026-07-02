"""
FinAgent-Lithium Full Pipeline Orchestrator.

Orchestrates all 8 workflow nodes in sequence.
Returns a complete AnalysisState dict ready for frontend rendering.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional, List, Any

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from nodes.classify_sector import classify_sector
from nodes.validate_data import validate_data
from nodes.calculate_general import calculate_general, annotate_formulas
from nodes.calculate_sector import calculate_sector
from nodes.filter_key_indicators import filter_key_indicators
from nodes.linkage_analysis import linkage_analysis
from nodes.anomaly_scan import anomaly_scan
from nodes.generate_report import generate_report, _generate_fallback_report, _build_input_json
from nodes.metric_config import (
    build_industry_comparison,
    calculate_weighted_score,
    enrich_metric_results,
    enrich_sector_indicator_results,
    flatten_indicator_results,
    summarize_metric_config,
)
from nodes.interpretation import build_structured_interpretations

try:
    from lithium_kb import LithiumKnowledgeBase
    _KB_PATH = _PROJECT_ROOT / "lithium_knowledge_base.json"
    KB = LithiumKnowledgeBase(_KB_PATH).load() if _KB_PATH.exists() else None
except Exception:
    KB = None


_DEFAULT_PEER_WINDCODES = [
    "300750.SZ",  # 宁德时代
    "002594.SZ",  # 比亚迪
    "300014.SZ",  # 亿纬锂能
    "688005.SH",  # 容百科技
    "002466.SZ",  # 天齐锂业
]


def _context_fetch_enabled(state: Dict[str, Any], financial_data: Dict[str, Any]) -> bool:
    if os.environ.get("WIND_ENABLE_CONTEXT") != "1":
        return False
    if not financial_data:
        return False
    if state.get("data_completeness", 0) <= 0:
        return False
    return bool(state.get("wind_status", {}).get("live_calls_enabled", True))


def _peer_windcodes_for_context(stock_code: str = "") -> List[str]:
    raw = os.environ.get("WIND_PEER_CODES", "").strip()
    if raw:
        codes = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        codes = list(_DEFAULT_PEER_WINDCODES)
    current = ""
    try:
        from nodes.wind_adapter import stock_code_to_windcode
        current = stock_code_to_windcode(stock_code) if stock_code else ""
    except Exception:
        current = ""
    peers = [code for code in codes if code != current]
    return peers


def run_pipeline(
    company_name: str = "",
    stock_code: str = "",
    current_period: str = "",
    primary_business: Optional[List[str]] = None,
    manual_sector: Optional[str] = None,
    financial_data: Optional[Dict[str, Optional[float]]] = None,
    notes_data: Optional[Dict[str, Optional[float]]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the complete 8-node FinAgent-Lithium analysis pipeline.

    Args:
        company_name: Company name.
        stock_code: Stock code (for lookup table).
        current_period: Report period label (e.g. "2025年报").
        primary_business: List of business segment descriptions.
        manual_sector: Optional manual sector code override.
        financial_data: Dict of {account_name: value}.
        notes_data: Dict of {note_account: value}.
        llm_config: LLM API config dict.

    Returns:
        Complete AnalysisState dict with all node outputs.
    """
    financial_data = financial_data or {}
    notes_data = notes_data or {}
    primary_business = primary_business or []

    def _emit(step: int, label: str) -> None:
        """Report pipeline stage to an optional progress callback (async UI)."""
        if progress_cb:
            try:
                progress_cb(step, label)
            except Exception:
                pass

    # ── Wind-enhanced sector classification ──
    # If stock_code is provided but no primary_business, try Wind for keywords.
    _wind_classified = False
    if stock_code and not primary_business and not company_name:
        try:
            from nodes.wind_adapter import (
                stock_code_to_windcode,
                fetch_company_info_enhanced,
                extract_business_keywords,
            )
            windcode = stock_code_to_windcode(stock_code)
            wind_info = fetch_company_info_enhanced(windcode, timeout=6)
            if wind_info:
                # Use Wind company name if user didn't provide one
                if not company_name:
                    company_name = wind_info.get("name", "")
                # Extract business keywords for sector matching
                wind_keywords = extract_business_keywords(wind_info)
                if wind_keywords:
                    primary_business = wind_keywords
                    _wind_classified = True
        except Exception:
            _wind_classified = False
            pass

    state: Dict[str, Any] = {
        "company_name": company_name,
        "stock_code": stock_code,
        "current_period": current_period,
        "financial_data": financial_data,
        "notes_data": notes_data,
        "primary_business": primary_business,
        "manual_sector": manual_sector,
        "_wind_classified": _wind_classified,
    }

    # Node 1: classify_sector
    _emit(1, "识别公司与行业赛道")
    sector = classify_sector(
        company_name=company_name, stock_code=stock_code,
        primary_business=primary_business, manual_sector=manual_sector, kb=KB)
    state.update(sector)

    # ── Wind data enrichment (optional, graceful degradation) ──
    # If stock code provided but financial data is sparse, try Wind.
    state["_wind_fetch_attempted"] = False
    state["_wind_enriched"] = False
    state["_wind_accounts_count"] = 0
    if stock_code and (not financial_data or len(financial_data) < 5):
        state["_wind_fetch_attempted"] = True
        try:
            from nodes.wind_adapter import stock_code_to_windcode, fetch_financials, get_wind_status
            windcode = stock_code_to_windcode(stock_code)
            wind_timeout = int(os.environ.get("WIND_FINANCIAL_TIMEOUT_SECONDS", "12"))
            wind_data = fetch_financials(windcode, period=current_period or "最新一期", timeout=wind_timeout)
            if wind_data:
                # Merge: Wind data as base, user-provided data on top
                merged = {**wind_data, **financial_data}
                financial_data = merged
                state["_wind_enriched"] = True
                state["_wind_accounts_count"] = len(wind_data)
            else:
                state["_wind_fetch_error"] = get_wind_status().get("last_error") or "Wind returned no financial data"
        except Exception as exc:
            state["_wind_fetch_error"] = str(exc)

    # ── DeepSeek supplement for critical missing accounts ──
    _CRITICAL_FOR_INDICATORS = {
        "扣非净利润": "扣非销售净利率、净利润现金含量、扣非ROE",
        "经营活动现金流净额": "净利润现金含量",
    }
    _PRIOR_FOR_YOY = {
        "上期营业收入": "营收同比增速",
        "期初应收账款": "应收账款同比增速",
        "期初存货": "存货同比增速",
        "期初净资产": "扣非ROE",
    }
    def _has_prior_alias(key: str) -> bool:
        """True if an alternate naming of a prior-period account already exists
        (e.g. '营业收入上期' for '上期营业收入'), so no LLM supplement needed."""
        if key.startswith("上期"):
            alts = (key[2:] + "上期",)
        elif key.startswith("期初"):
            alts = (key[2:] + "期初", "上期" + key[2:])
        else:
            return False
        return any(financial_data.get(a) is not None for a in alts)

    missing_critical = [k for k in _CRITICAL_FOR_INDICATORS if financial_data.get(k) is None]
    missing_prior = [k for k in _PRIOR_FOR_YOY
                     if financial_data.get(k) is None and not _has_prior_alias(k)]
    all_missing = missing_critical + missing_prior

    if all_missing and (company_name or stock_code):
        try:
            from nodes.llm_client import call_llm
            accounts_str = "、".join(all_missing)
            supplement_prompt = (
                "你是财务数据查询工具。请返回以下公司的指定财务科目数据。\n\n"
                "## 要求\n"
                "1. 只返回 JSON，格式: {\"科目名\": 数值(元)}\n"
                "2. 数值必须是元为单位的数字，不带单位文字\n"
                "3. 无法确定的写 null\n"
                "4. 只输出 JSON，不加任何说明或 markdown\n\n"
                f"## 查询\n公司：{company_name}，股票代码：{stock_code}，报告期：{current_period}\n"
                f"需要科目：{accounts_str}"
            )
            resp = call_llm(supplement_prompt, "", config={"timeout": 15})
            if resp and len(resp.strip()) > 5:
                cleaned = resp.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                supplement_data = json.loads(cleaned)
                for key, val in supplement_data.items():
                    if val is not None and key in all_missing and financial_data.get(key) is None:
                        financial_data[key] = float(val)
                        state.setdefault("_deepseek_supplemented", []).append(key)
        except Exception:
            pass

    # Update state with possibly enriched financial_data
    state["financial_data"] = financial_data

    # Node 2: validate_data
    _emit(2, "校验财务数据")
    validation = validate_data(
        financial_data=financial_data, notes_data=notes_data,
        sector_level1=state.get("sector_level1"),
        sector_level2=state.get("sector_level2"),
        sub_sectors=state.get("sub_sectors"), kb=KB)
    state.update(validation)

    effective_llm_config = llm_config
    if not financial_data or state.get("data_completeness", 0) <= 0:
        effective_llm_config = {**(llm_config or {}), "api_key": ""}

    # Surface whether LLM sections are real or mock placeholders (no key ->
    # llm_client returns fabricated demo content; the report must say so).
    from nodes.llm_client import DEFAULT_CONFIG as _llm_defaults
    if effective_llm_config is not None and "api_key" in effective_llm_config:
        state["_llm_available"] = bool(effective_llm_config["api_key"])
    else:
        state["_llm_available"] = bool(_llm_defaults.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"))

    # Node 3: calculate_general
    _emit(3, "计算指标与评分")
    general = calculate_general(financial_data=financial_data, notes_data=notes_data)
    general_inds = annotate_formulas(general["general_indicators"])
    state["general_indicators"] = enrich_metric_results(
        state.get("_sector_code"), general_inds
    )

    # Node 4: calculate_sector
    sector_inds = calculate_sector(
        financial_data=financial_data, notes_data=notes_data,
        sector_level2=state.get("sector_level2"),
        sub_sectors=state.get("sub_sectors"),
        _sector_code=state.get("_sector_code"), kb=KB)
    state["sector_indicators"] = enrich_sector_indicator_results(
        sector_inds["sector_indicators"]
    )
    state["metric_config_summary"] = summarize_metric_config(state.get("_sector_code"))
    state["weighted_score"] = calculate_weighted_score(
        flatten_indicator_results(
            general_indicators=state["general_indicators"],
            sector_indicators=state["sector_indicators"],
        )
    )

    # ── Historical data fetch (optional, for trend charts) ──
    state["historical_data"] = {}
    hist_years = int(os.environ.get("HISTORICAL_YEARS", "5"))
    if stock_code and current_period and hist_years > 0:
        try:
            from nodes.historical_data import fetch_historical_periods
            from nodes.wind_adapter import stock_code_to_windcode
            windcode = stock_code_to_windcode(stock_code)
            historical = fetch_historical_periods(windcode, current_period, years=hist_years)
            if historical and historical.get("periods"):
                state["historical_data"] = historical
        except Exception as exc:
            state["_historical_error"] = str(exc)

    # ── Market data fetch (price / market cap / shares, for valuation models) ──
    state["market_data"] = {}
    if stock_code:
        try:
            from nodes.wind_adapter import fetch_market_data, stock_code_to_windcode
            windcode = stock_code_to_windcode(stock_code)
            mkt_timeout = int(os.environ.get("WIND_CONTEXT_TIMEOUT_SECONDS", "6"))
            market = fetch_market_data(windcode, timeout=mkt_timeout)
            if market:
                state["market_data"] = market
        except Exception as exc:
            state["_market_data_error"] = str(exc)

    # ── Wind: Macro + Peer context (optional) ──
    try:
        from nodes.wind_adapter import (
            get_lithium_price_trend, fetch_peers_financials, get_wind_status, stock_code_to_windcode,
        )
        state["wind_status"] = get_wind_status()
        if _context_fetch_enabled(state, financial_data):
            # Lithium price trend for anomaly external rules
            context_timeout = int(os.environ.get("WIND_CONTEXT_TIMEOUT_SECONDS", "6"))
            lithium_trend = get_lithium_price_trend(timeout=context_timeout)
            if lithium_trend:
                state["_lithium_trend"] = lithium_trend
                state["macro_context"] = {"lithium_trend": lithium_trend}

            # Peer financials for cross-section comparison
            peer_limit = int(os.environ.get("WIND_PEER_MAX", "2"))
            peer_codes = _peer_windcodes_for_context(stock_code)
            peers = fetch_peers_financials(
                windcodes=peer_codes,
                max_peers=peer_limit,
                timeout=context_timeout,
            )
            if peers:
                # Tag the current company
                current_wc = stock_code_to_windcode(stock_code) if stock_code else ""
                for p in peers:
                    p["is_current"] = (p["windcode"] == current_wc)
                state["_peer_comparison"] = peers
    except Exception:
        pass

    state.setdefault("macro_context", {})
    if "wind_status" not in state:
        try:
            from nodes.wind_adapter import get_wind_status
            state["wind_status"] = get_wind_status()
        except Exception:
            state["wind_status"] = {"live_calls_enabled": False, "last_error": "Wind adapter unavailable"}

    # DeepSeek macro context fallback when Wind data is unavailable
    if not state.get("_lithium_trend") and not state.get("macro_context", {}).get("lithium_trend"):
        try:
            from nodes.llm_client import call_llm
            sector_name = state.get("sector_level2") or "锂电池"
            macro_prompt = (
                "你是一位锂电行业宏观分析师。请基于以下公司背景，分析该公司所处行业的宏观环境。\n\n"
                "## 要求\n"
                "用 JSON 格式输出，字段如下：\n"
                "{\n"
                '  "lithium_price_trend": "碳酸锂价格近期走势描述（含具体价格区间和涨跌幅）",\n'
                '  "supply_demand": "锂电池上下游供需格局变化",\n'
                '  "policy_environment": "影响行业的国内外政策（补贴、产能管控、出口关税等）",\n'
                '  "industry_growth": "行业整体增速和产能利用率趋势",\n'
                '  "impact_on_company": "以上宏观因素对该公司细分赛道的具体影响",\n'
                '  "summary": "一段100-150字的综合宏观分析摘要"\n'
                "}\n\n"
                "## 约束\n"
                "1. 只陈述可验证的事实和趋势，不做投资建议\n"
                "2. 引用具体数字时标明来源年份/季度\n"
                "3. 如果某个字段信息不足，写'暂无可靠数据'而非编造\n"
                "4. 只输出 JSON，不要加 markdown 代码块"
            )
            macro_response = call_llm(
                macro_prompt,
                f"公司：{company_name}，细分赛道：{sector_name}，报告期：{current_period}",
                config={"timeout": 20},
            )
            if macro_response and len(macro_response.strip()) > 30:
                cleaned = macro_response.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                try:
                    macro_data = json.loads(cleaned)
                    state["_deepseek_macro_structured"] = macro_data
                    state["_deepseek_macro"] = macro_data.get("summary", cleaned)
                    state["macro_context"].update({
                        k: v for k, v in macro_data.items()
                        if k != "summary" and v and v != "暂无可靠数据"
                    })
                except (ValueError, KeyError):
                    state["_deepseek_macro"] = cleaned
        except Exception:
            pass

    state["industry_comparison"] = build_industry_comparison(
        flatten_indicator_results(
            general_indicators=state["general_indicators"],
            sector_indicators=state["sector_indicators"],
        ),
        state.get("_peer_comparison", []),
    )

    # Node 5: filter_key_indicators
    key_inds = filter_key_indicators(
        general_indicators=state["general_indicators"],
        sector_indicators=state["sector_indicators"],
        _sector_code=state.get("_sector_code"),
        sub_sectors=state.get("sub_sectors"), kb=KB)
    state["key_indicators_for_linkage"] = key_inds["key_indicators_for_linkage"]

    # Node 6: linkage_analysis
    _emit(4, "异常扫描与行业对标")
    linkage = linkage_analysis(
        sector_level2=state.get("sector_level2"),
        sub_sectors=state.get("sub_sectors"),
        key_indicators_for_linkage=state["key_indicators_for_linkage"],
        general_indicators=state["general_indicators"],
        sector_indicators=state["sector_indicators"],
        sector_characteristics=state.get("sector_characteristics"),
        analysis_focus=state.get("analysis_focus"), kb=KB,
        llm_config=effective_llm_config)
    state["linkage_diagnosis"] = linkage["linkage_diagnosis"]

    # Node 7: anomaly_scan (with macro context for external rules)
    anomaly = anomaly_scan(
        general_indicators=state["general_indicators"],
        sector_indicators=state["sector_indicators"],
        financial_data=financial_data,
        _sector_code=state.get("_sector_code"),
        sub_sectors=state.get("sub_sectors"), kb=KB,
        macro_context=state.get("_lithium_trend") or state.get("macro_context"))
    state["anomaly_signals"] = anomaly["anomaly_signals"]
    state["skipped_external_rules"] = anomaly.get("skipped_external_rules", 0)

    interpretations = build_structured_interpretations(
        general_indicators=state["general_indicators"],
        sector_indicators=state["sector_indicators"],
        anomaly_signals=state["anomaly_signals"],
        macro_context=state.get("macro_context", {}),
        industry_comparison=state.get("industry_comparison", {}),
        weighted_score=state.get("weighted_score"),
        llm_config=effective_llm_config,
    )
    state.update(interpretations)

    # Node 8: generate_report
    _emit(5, "生成评分与报告")
    report = generate_report(
        company_name=company_name, stock_code=stock_code,
        current_period=current_period,
        sector_level1=state.get("sector_level1"),
        sector_level2=state.get("sector_level2"),
        sector_characteristics=state.get("sector_characteristics"),
        analysis_focus=state.get("analysis_focus"),
        sub_sectors=state.get("sub_sectors"),
        general_indicators=state["general_indicators"],
        sector_indicators=state["sector_indicators"],
        key_indicators_for_linkage=state["key_indicators_for_linkage"],
        linkage_diagnosis=state["linkage_diagnosis"],
        anomaly_signals=state["anomaly_signals"],
        error_log=state.get("error_log", []),
        data_completeness=state.get("data_completeness"),
        skipped_external_rules=state.get("skipped_external_rules", 0),
        weighted_score=state.get("weighted_score"),
        metric_interpretations=state.get("metric_interpretations"),
        macro_insights=state.get("macro_insights"),
        industry_benchmark_insights=state.get("industry_benchmark_insights"),
        risk_summary=state.get("risk_summary"),
        llm_config=effective_llm_config)
    state["report_markdown"] = report["report_markdown"]

    # ── AI strategy insights (optional, post-report) ──
    state["strategy_insights"] = {}
    if effective_llm_config and effective_llm_config.get("api_key", ""):
        try:
            from nodes.llm_client import call_llm
            strategy_prompt = (
                "你是资深行业分析师。基于以下财报分析报告，提取公司的战略方向和展望。\n\n"
                "## 要求\n"
                "返回 JSON，格式如下：\n"
                "{\n"
                '  "strategy_summary": "一段50-100字的战略方向总结",\n'
                '  "key_strategies": ["战略要点1", "战略要点2", "战略要点3"],\n'
                '  "outlook": "未来1-2年展望（50-80字）",\n'
                '  "risks": ["关键风险1", "关键风险2"]\n'
                "}\n\n"
                "## 约束\n"
                "1. 只基于报告内容提取，不编造未提及的信息\n"
                "2. 战略要点最多5条，风险最多3条\n"
                "3. 只输出 JSON，不加 markdown 代码块\n"
            )
            report_excerpt = state["report_markdown"][:3000]
            resp = call_llm(strategy_prompt, report_excerpt, config={"timeout": 20})
            if resp and len(resp.strip()) > 20:
                cleaned = resp.strip()
                if cleaned.startswith("```"):
                    cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                state["strategy_insights"] = json.loads(cleaned)
        except Exception:
            pass

    # ── Advanced analysis models (杜邦/CVP/现金流/Z-score/DCF/相对估值/哈佛/EVA) ──
    try:
        from nodes.advanced_models import run_advanced_analysis
        state["advanced_analysis"] = run_advanced_analysis(state, effective_llm_config)
    except Exception as exc:
        state["advanced_analysis"] = {}
        state["_advanced_error"] = str(exc)

    # ── Six-dimension trend prediction (optional, needs historical series) ──
    try:
        from nodes.prediction_model import run_prediction_analysis
        state["prediction_analysis"] = run_prediction_analysis(state, effective_llm_config)
    except Exception as exc:
        state["prediction_analysis"] = {"available": False}
        state["_prediction_error"] = str(exc)

    state["status"] = "completed"
    return state

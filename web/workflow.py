"""
FinAgent-Lithium Full Pipeline Orchestrator.

Orchestrates all 8 workflow nodes in sequence.
Returns a complete AnalysisState dict ready for frontend rendering.
"""

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

    # Update state with possibly enriched financial_data
    state["financial_data"] = financial_data

    # Node 2: validate_data
    validation = validate_data(
        financial_data=financial_data, notes_data=notes_data,
        sector_level1=state.get("sector_level1"),
        sector_level2=state.get("sector_level2"),
        sub_sectors=state.get("sub_sectors"), kb=KB)
    state.update(validation)

    effective_llm_config = llm_config
    if not financial_data or state.get("data_completeness", 0) <= 0:
        effective_llm_config = {**(llm_config or {}), "api_key": ""}

    # Node 3: calculate_general
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
        macro_context=state.get("_lithium_trend"))
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

    state["status"] = "completed"
    return state

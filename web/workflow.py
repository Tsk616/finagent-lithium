"""
FinAgent-Lithium Full Pipeline Orchestrator.

Orchestrates all 8 workflow nodes in sequence.
Returns a complete AnalysisState dict ready for frontend rendering.
"""

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

try:
    from lithium_kb import LithiumKnowledgeBase
    _KB_PATH = _PROJECT_ROOT / "lithium_knowledge_base.json"
    KB = LithiumKnowledgeBase(_KB_PATH).load() if _KB_PATH.exists() else None
except Exception:
    KB = None


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

    state: Dict[str, Any] = {
        "company_name": company_name,
        "stock_code": stock_code,
        "current_period": current_period,
        "financial_data": financial_data,
        "notes_data": notes_data,
        "primary_business": primary_business,
        "manual_sector": manual_sector,
    }

    # Node 1: classify_sector
    sector = classify_sector(
        company_name=company_name, stock_code=stock_code,
        primary_business=primary_business, manual_sector=manual_sector, kb=KB)
    state.update(sector)

    # Node 2: validate_data
    validation = validate_data(
        financial_data=financial_data, notes_data=notes_data,
        sector_level1=state.get("sector_level1"),
        sector_level2=state.get("sector_level2"),
        sub_sectors=state.get("sub_sectors"), kb=KB)
    state.update(validation)

    # Node 3: calculate_general
    general = calculate_general(financial_data=financial_data, notes_data=notes_data)
    general_inds = annotate_formulas(general["general_indicators"])
    state["general_indicators"] = general_inds

    # Node 4: calculate_sector
    sector_inds = calculate_sector(
        financial_data=financial_data, notes_data=notes_data,
        sector_level2=state.get("sector_level2"),
        sub_sectors=state.get("sub_sectors"),
        _sector_code=state.get("_sector_code"), kb=KB)
    state["sector_indicators"] = sector_inds["sector_indicators"]

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
        llm_config=llm_config)
    state["linkage_diagnosis"] = linkage["linkage_diagnosis"]

    # Node 7: anomaly_scan
    anomaly = anomaly_scan(
        general_indicators=state["general_indicators"],
        sector_indicators=state["sector_indicators"],
        financial_data=financial_data,
        _sector_code=state.get("_sector_code"),
        sub_sectors=state.get("sub_sectors"), kb=KB)
    state["anomaly_signals"] = anomaly["anomaly_signals"]
    state["skipped_external_rules"] = anomaly.get("skipped_external_rules", 0)

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
        llm_config=llm_config)
    state["report_markdown"] = report["report_markdown"]

    state["status"] = "completed"
    return state

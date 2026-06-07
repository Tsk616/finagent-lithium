#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FinAgent-Lithium Knowledge Base Loader
=======================================
Loads, validates, and queries lithium_knowledge_base.json.

Design principles:
  - Accept both file path (str) and pre-loaded dict (for Excel→JSON pipeline
    compatibility: the converter produces a dict, this loader consumes it
    without requiring an intermediate file write).
  - Lazy validation: load() always succeeds; call validate() explicitly to
    get a structured report of issues.
  - Key normalization: query methods normalize sector codes and indicator
    names so callers don't need to worry about exact string matching.
  - Graceful degradation: missing keys return None rather than throwing,
    so the workflow can handle "data missing" scenarios at the node level.

Usage:
    kb = LithiumKnowledgeBase("lithium_knowledge_base.json").load()
    report = kb.validate()
    if report["errors"]:
        print("Fix these before running the workflow:", report["errors"])

    sector = kb.get_sector("1.1")
    indicator = kb.get_indicator("1.1", "存货周转率")
    canonical = kb.resolve_account("Revenue")  # → "营业收入"
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple


# ── Threshold parsing helpers ──────────────────────────────────────────

# Maps the _parsed.type values from excel_to_json.py
PARSED_TYPE_ORDERED = {
    "lt": 0,    # <X
    "lte": 1,   # <=X
    "range": 2, # X~Y
    "gte": 3,   # >=X
    "gt": 4,    # >X
}


def _parse_single_threshold(parsed: dict) -> Optional[Tuple[float, float, bool, bool]]:
    """Convert a _parsed threshold dict into (low, high, low_inclusive, high_inclusive).

    Returns None for 'raw' type (unparseable compound thresholds like "5~9 次 / 40~73 天").
    """
    if not parsed or not isinstance(parsed, dict):
        return None
    t = parsed.get("type", "raw")
    try:
        if t == "range":
            return (float(parsed["low"]), float(parsed["high"]), True, True)
        elif t == "gte":
            return (float(parsed["value"]), float("inf"), True, True)
        elif t == "gt":
            return (float(parsed["value"]), float("inf"), False, True)
        elif t == "lte":
            return (float("-inf"), float(parsed["value"]), True, True)
        elif t == "lt":
            return (float("-inf"), float(parsed["value"]), True, False)
        elif t in ("abs_range", "below_baseline"):
            # Benchmark-relative; can't validate overlap without the baseline value
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return None


def _intervals_overlap(a: Tuple[float, float, bool, bool],
                       b: Tuple[float, float, bool, bool]) -> bool:
    """Check if two parsed threshold intervals have overlapping values."""
    a_low, a_high, a_li, a_hi = a
    b_low, b_high, b_li, b_hi = b

    max_low = max(a_low, b_low)
    min_high = min(a_high, b_high)

    if max_low < min_high:
        return True
    if max_low == min_high:
        # Point overlap — check inclusivity on both sides
        low_inc = (a_low == max_low and a_li) or (b_low == max_low and b_li)
        high_inc = (a_high == min_high and a_hi) or (b_high == min_high and b_hi)
        return low_inc and high_inc
    return False


# ── Main loader class ──────────────────────────────────────────────────

class LithiumKnowledgeBase:
    """Load and query the lithium industry financial analysis knowledge base."""

    def __init__(self, source: Union[str, Path, dict]):
        """
        Args:
            source: Either a file path to lithium_knowledge_base.json,
                    or a pre-loaded dict (from Excel→JSON converter).
        """
        self._source = source
        self._data: Dict[str, Any] = {}
        self._loaded = False

        # Fast lookup indexes (built during load())
        self._general_index: Dict[str, dict] = {}       # indicator_name → indicator
        self._alias_to_canonical: Dict[str, str] = {}    # normalized_alias → canonical_account_name

    # ── Loading ──────────────────────────────────────────────────────

    def load(self) -> "LithiumKnowledgeBase":
        """Load and index the knowledge base. Returns self for chaining."""
        if isinstance(self._source, (str, Path)):
            path = Path(self._source)
            if not path.exists():
                raise FileNotFoundError(f"Knowledge base file not found: {path}")
            with open(path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        elif isinstance(self._source, dict):
            self._data = self._source
        else:
            raise TypeError(f"source must be str, Path, or dict, got {type(self._source)}")

        self._build_indexes()
        self._loaded = True
        return self

    def _build_indexes(self) -> None:
        """Build in-memory lookup indexes for O(1) queries."""
        # General indicators: name → indicator
        for ind in self._data.get("general_indicators", []):
            name = ind.get("name", "")
            if name:
                self._general_index[name] = ind

        # Account mapping: each alias → canonical name
        # Phase 1: Map each canonical name to itself (self-mapping takes priority)
        for canonical in self._data.get("account_mapping", {}):
            ck = self._normalize_str(canonical)
            if ck:
                self._alias_to_canonical[ck] = canonical

        # Phase 2: Map aliases, never overwrite existing entries
        # (self-mappings and first-come aliases win over conflicts)
        for canonical, aliases in self._data.get("account_mapping", {}).items():
            for alias in aliases:
                key = self._normalize_str(alias)
                if key and key not in self._alias_to_canonical:
                    self._alias_to_canonical[key] = canonical

    @staticmethod
    def _normalize_str(s: str) -> str:
        """Normalize a string for case-insensitive, whitespace-insensitive matching."""
        return s.strip().lower() if s else ""

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ── Query interface ──────────────────────────────────────────────

    def get_sector(self, code: str) -> Optional[dict]:
        """Return the full sector dict for a sector code (e.g. '1.1', '3.2')."""
        return self._data.get("sectors", {}).get(code)

    def get_indicator(self, sector_code: str, indicator_name: str) -> Optional[dict]:
        """Return a specific indicator dict from a sector, searching all categories."""
        sector = self.get_sector(sector_code)
        if not sector:
            return None
        for category, indicators in sector.get("indicators", {}).items():
            for ind in indicators:
                if ind.get("name") == indicator_name or indicator_name in ind.get("name", ""):
                    return ind
        return None

    def get_sector_indicators(self, sector_code: str) -> Dict[str, List[dict]]:
        """Return all indicators for a sector, grouped by category.

        Returns:
            Dict like {'营运效率类': [...], '盈利能力类': [...], ...}
            Empty dict if sector not found.
        """
        sector = self.get_sector(sector_code)
        if not sector:
            return {}
        return sector.get("indicators", {})

    def get_all_sector_indicators_flat(self, sector_code: str) -> List[dict]:
        """Return all indicators for a sector as a flat list."""
        result = []
        for indicators in self.get_sector_indicators(sector_code).values():
            result.extend(indicators)
        return result

    def get_general_indicator(self, name: str) -> Optional[dict]:
        """Return a general indicator by name (supports partial match)."""
        if name in self._general_index:
            return self._general_index[name]
        # Partial match fallback
        for key, val in self._general_index.items():
            if name in key or key in name:
                return val
        return None

    def get_general_indicators(self) -> List[dict]:
        """Return all general indicators."""
        return self._data.get("general_indicators", [])

    def get_linkage_rules(self, sector_code: str) -> List[dict]:
        """Return linkage rules for a sector."""
        sector = self.get_sector(sector_code)
        if not sector:
            return []
        return sector.get("linkage_rules", [])

    def get_anomaly_rules(self, sector_code: str,
                          include_external: bool = False) -> List[dict]:
        """Return anomaly rules for a sector.

        Args:
            sector_code: Sector code.
            include_external: If False (default), skip rules that require
                              external data (锂价, 行业增速, etc.).
        """
        sector = self.get_sector(sector_code)
        if not sector:
            return []
        rules = sector.get("anomaly_rules", [])
        if not include_external:
            rules = [r for r in rules if not r.get("requires_external", False)]
        return rules

    def resolve_account(self, name: str) -> Optional[str]:
        """Resolve an account name through the alias mapping.

        Args:
            name: Raw account name from financial data.

        Returns:
            Canonical account name (e.g. '营业收入'), or None if unrecognized.
        """
        key = self._normalize_str(name)
        # Exact match
        if key in self._alias_to_canonical:
            return self._alias_to_canonical[key]
        # Try stripping common suffixes
        for suffix in ["本期", "期末", "期初", "余额", "净额", "合计", "总额"]:
            stripped = key.replace(self._normalize_str(suffix), "")
            if stripped in self._alias_to_canonical:
                return self._alias_to_canonical[stripped]
        return None

    def resolve_accounts_batch(self, names: List[str]) -> Dict[str, Optional[str]]:
        """Batch-resolve account names. Maps input name → canonical name or None."""
        return {name: self.resolve_account(name) for name in names}

    def list_sectors(self) -> List[str]:
        """Return all sector codes."""
        return list(self._data.get("sectors", {}).keys())

    def list_sector_names(self) -> Dict[str, str]:
        """Return {code: human_name} for all sectors."""
        return {
            code: sector.get("name", code)
            for code, sector in self._data.get("sectors", {}).items()
        }

    def get_raw(self) -> Dict[str, Any]:
        """Return the raw loaded data dict (for advanced use cases)."""
        return self._data

    # ── Validation ───────────────────────────────────────────────────

    def validate(self) -> Dict[str, Any]:
        """Run comprehensive validation and return a structured report.

        Returns:
            {
                "valid": bool,
                "errors": [(location, message), ...],     # must fix
                "warnings": [(location, message), ...],   # should fix
                "stats": {"sectors": N, "general_indicators": N, ...}
            }
        """
        errors: List[Tuple[str, str]] = []
        warnings: List[Tuple[str, str]] = []

        if not self._loaded:
            errors.append(("loader", "Knowledge base not loaded. Call .load() first."))
            return {"valid": False, "errors": errors, "warnings": warnings, "stats": {}}

        data = self._data

        # ── Top-level structure ──
        for key in ["sectors", "general_indicators", "account_mapping"]:
            if key not in data:
                errors.append(("top_level", f"Missing required key: '{key}'"))
            elif not isinstance(data[key], (dict, list)):
                errors.append(("top_level", f"'{key}' must be dict or list, got {type(data[key]).__name__}"))

        # ── Sectors ──
        sector_codes = []
        if "sectors" in data and isinstance(data["sectors"], dict):
            sector_codes = list(data["sectors"].keys())
            for code, sector in data["sectors"].items():
                self._validate_sector(code, sector, errors, warnings)

        # ── General indicators ──
        if "general_indicators" in data and isinstance(data["general_indicators"], list):
            for i, ind in enumerate(data["general_indicators"]):
                ind_warnings, _ = self._validate_indicator(
                    f"general_indicators[{i}] ({ind.get('name', '?')})", ind)
                for w in ind_warnings:
                    warnings.append(w)

        # ── Account mapping ──
        if "account_mapping" in data and isinstance(data["account_mapping"], dict):
            if len(data["account_mapping"]) == 0:
                warnings.append(("account_mapping", "Account mapping is empty — alias resolution will fail for all accounts."))
        else:
            errors.append(("account_mapping", "Missing or invalid account_mapping."))

        stats = {
            "sectors": len(sector_codes),
            "general_indicators": len(data.get("general_indicators", [])),
            "account_mapping_entries": len(data.get("account_mapping", {})),
        }

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "stats": stats,
        }

    def _validate_sector(self, code: str, sector: dict,
                         errors: list, warnings: list) -> None:
        """Validate a single sector entry. Groups repetitive issues into summary warnings."""
        prefix = f"sector {code}"

        for field in ["name", "characteristics", "focus"]:
            if not sector.get(field):
                warnings.append((prefix, f"Missing '{field}' — analysis quality may degrade."))

        indicators = sector.get("indicators", {})
        if not indicators:
            errors.append((prefix, "No indicators defined."))
        else:
            # Counters for grouped warnings
            missing_accounts = 0
            missing_key_flag = 0
            missing_fatal_flag = 0
            overlap_issues = 0
            formula_issues = 0
            total_inds = 0

            for category, ind_list in indicators.items():
                if not isinstance(ind_list, list):
                    errors.append((prefix, f"Indicator category '{category}' is not a list."))
                    continue
                for ind in ind_list:
                    total_inds += 1
                    # Run individual indicator validation (threshold overlap, etc.)
                    ind_warnings, ind_stats = self._validate_indicator(
                        f"{prefix}/{category}/{ind.get('name', '?')}", ind)
                    for w in ind_warnings:
                        warnings.append(w)
                    if ind_stats.get("accounts_empty"):
                        missing_accounts += 1
                    if ind_stats.get("missing_key_flag"):
                        missing_key_flag += 1
                    if ind_stats.get("missing_fatal_flag"):
                        missing_fatal_flag += 1
                    if ind_stats.get("overlap_issue"):
                        overlap_issues += 1
                    if ind_stats.get("formula_issue"):
                        formula_issues += 1

            # Emit grouped summary warnings (one per sector, not per indicator)
            if missing_accounts:
                warnings.append(
                    (f"{prefix}/indicators",
                     f"{missing_accounts}/{total_inds} indicators have empty accounts_needed "
                     f"— they cannot be computed until accounts are populated."))
            if missing_key_flag:
                warnings.append(
                    (f"{prefix}/indicators",
                     f"{missing_key_flag}/{total_inds} indicators missing is_key_indicator flag."))
            if missing_fatal_flag:
                warnings.append(
                    (f"{prefix}/indicators",
                     f"{missing_fatal_flag}/{total_inds} indicators missing is_fatal_indicator flag."))
            if overlap_issues:
                warnings.append(
                    (f"{prefix}/indicators",
                     f"{overlap_issues} indicators have overlapping threshold ranges."))
            if formula_issues:
                warnings.append(
                    (f"{prefix}/indicators",
                     f"{formula_issues} indicators reference unrecognized account names in formula."))

        # Linkage rules
        linkage = sector.get("linkage_rules", [])
        linkage_missing_indicators = 0
        linkage_missing_fields = 0
        for i, rule in enumerate(linkage):
            for field in ["combination", "status", "description"]:
                if not rule.get(field):
                    linkage_missing_fields += 1
                    break
            if not rule.get("indicators_needed") and not rule.get("raw"):
                linkage_missing_indicators += 1
        if linkage:
            if linkage_missing_fields:
                warnings.append(
                    (f"{prefix}/linkage_rules",
                     f"{linkage_missing_fields}/{len(linkage)} rules missing required fields."))
            if linkage_missing_indicators:
                warnings.append(
                    (f"{prefix}/linkage_rules",
                     f"{linkage_missing_indicators}/{len(linkage)} rules missing "
                     f"both 'indicators_needed' and 'raw' — cannot be evaluated."))

        # Anomaly rules
        anomaly = sector.get("anomaly_rules", [])
        anom_missing_id = 0
        anom_missing_check = 0
        anom_missing_severity = 0
        for i, rule in enumerate(anomaly):
            for field in ["description", "risk"]:
                if not rule.get(field):
                    warnings.append((f"{prefix}/anomaly_rules[{i}]", f"Missing '{field}'."))
            if "requires_external" not in rule:
                warnings.append((f"{prefix}/anomaly_rules[{i}]",
                                 "Missing 'requires_external' — defaulting to False."))
            if "id" not in rule:
                anom_missing_id += 1
            if "check" not in rule:
                anom_missing_check += 1
            if "severity" not in rule:
                anom_missing_severity += 1
        if anomaly:
            missing_parts = []
            if anom_missing_id:
                missing_parts.append("id")
            if anom_missing_check:
                missing_parts.append("check")
            if anom_missing_severity:
                missing_parts.append("severity")
            if missing_parts:
                warnings.append(
                    (f"{prefix}/anomaly_rules",
                     f"Missing spec fields ({', '.join(missing_parts)}) in "
                     f"{max(anom_missing_id, anom_missing_check, anom_missing_severity)}/"
                     f"{len(anomaly)} rules — rules may not be executable."))

    def _validate_indicator(self, location: str, ind: dict) -> Tuple[List[Tuple[str, str]], dict]:
        """Validate a single indicator entry.

        Returns:
            (warnings_list, stats_dict) where stats_dict has boolean flags:
            accounts_empty, missing_key_flag, missing_fatal_flag, overlap_issue, formula_issue
        """
        warnings: List[Tuple[str, str]] = []
        stats = {
            "accounts_empty": False,
            "missing_key_flag": False,
            "missing_fatal_flag": False,
            "overlap_issue": False,
            "formula_issue": False,
        }

        # Required fields
        for field in ["name", "formula", "unit", "single_mapping"]:
            if not ind.get(field):
                warnings.append((location, f"Missing '{field}'."))

        # Threshold fields: support both spec names (normal/warning/high_risk)
        # and current JSON names (normal_range/warning_range/high_risk_range)
        thr_normal = ind.get("normal") or ind.get("normal_range")
        thr_warning = ind.get("warning") or ind.get("warning_range")
        thr_high = ind.get("high_risk") or ind.get("high_risk_range")

        if not thr_normal or not thr_warning or not thr_high:
            warnings.append((location, "Missing threshold ranges."))

        # accounts_needed populated?
        if not ind.get("accounts_needed"):
            stats["accounts_empty"] = True

        # is_key_indicator / is_fatal_indicator present?
        if "is_key_indicator" not in ind:
            stats["missing_key_flag"] = True
        if "is_fatal_indicator" not in ind:
            stats["missing_fatal_flag"] = True

        # ── Threshold overlap check ──
        p_normal = ind.get("normal_parsed")
        p_warning = ind.get("warning_parsed")
        p_high = ind.get("high_risk_parsed")

        # normal vs warning overlap
        a = _parse_single_threshold(p_normal)
        b = _parse_single_threshold(p_warning)
        if a and b and _intervals_overlap(a, b):
            stats["overlap_issue"] = True
            warnings.append(
                (location,
                 f"Threshold overlap: normal_range ({thr_normal}) overlaps with "
                 f"warning_range ({thr_warning})."))

        # warning vs high_risk overlap
        c = _parse_single_threshold(p_high)
        if b and c and _intervals_overlap(b, c):
            stats["overlap_issue"] = True
            warnings.append(
                (location,
                 f"Threshold overlap: warning_range ({thr_warning}) overlaps with "
                 f"high_risk_range ({thr_high})."))

        # ── Formula references real accounts? ──
        formula = ind.get("formula", "")
        if formula and self._alias_to_canonical:
            tokens = self._extract_account_tokens(formula)
            for token in tokens:
                if not self.resolve_account(token):
                    stats["formula_issue"] = True
                    warnings.append(
                        (location,
                         f"Formula references '{token}' which is not in account_mapping."))

        return warnings, stats

    def _extract_account_tokens(self, formula: str) -> List[str]:
        """Extract potential Chinese account name tokens from a formula string.

        Heuristic: Chinese text segments separated by operators/numbers/punctuation.
        """
        # Split on operators, numbers, and common formula characters
        parts = re.split(r'[÷+\-×*/()（）\d.%=\s\n;；，,]+', formula)
        tokens = []
        for p in parts:
            p = p.strip()
            # Must contain at least one Chinese character (likely an account name)
            if p and re.search(r'[一-鿿]', p):
                tokens.append(p)
        return tokens

    # ── Reporting ────────────────────────────────────────────────────

    def print_validation_report(self) -> None:
        """Print a human-readable validation report to stdout."""
        report = self.validate()
        print(f"Knowledge Base Validation Report")
        print(f"  Sectors: {report['stats'].get('sectors', 0)}")
        print(f"  General indicators: {report['stats'].get('general_indicators', 0)}")
        print(f"  Account mapping entries: {report['stats'].get('account_mapping_entries', 0)}")
        print(f"  Errors: {len(report['errors'])}")
        print(f"  Warnings: {len(report['warnings'])}")
        print(f"  Overall: {'[VALID]' if report['valid'] else '[INVALID] (errors found)'}")

        if report["errors"]:
            print(f"\n  -- ERRORS --")
            for loc, msg in report["errors"]:
                print(f"    [{loc}] {msg}")

        if report["warnings"]:
            print(f"\n  -- WARNINGS --")
            for loc, msg in report["warnings"]:
                print(f"    [{loc}] {msg}")


# ── Mock data generator ───────────────────────────────────────────────

def generate_mock_kb() -> dict:
    """Generate a minimal valid knowledge base for testing loader logic.

    Covers 2 sectors (1.1 and 2.1) with a few indicators each,
    plus general indicators, linkage rules, anomaly rules, and account mapping.
    """
    return {
        "sectors": {
            "1.1": {
                "name": "锂资源开采 & 锂盐（碳酸锂 / 氢氧化锂）",
                "characteristics": "大宗商品属性，利润高度绑定锂价周期，盈利波动极强",
                "focus": "存货跌价准备、固定资产、合同负债、经营现金流",
                "indicators": {
                    "营运效率类": [
                        {
                            "name": "存货周转率",
                            "formula": "营业成本 ÷ 平均存货",
                            "unit": "次",
                            "normal_range": "5~9",
                            "warning_range": "4~5",
                            "high_risk_range": "<4",
                            "normal_parsed": {"raw": "5~9", "type": "range", "low": 5.0, "high": 9.0},
                            "warning_parsed": {"raw": "4~5", "type": "range", "low": 4.0, "high": 5.0},
                            "high_risk_parsed": {"raw": "<4", "type": "lt", "value": 4.0},
                            "single_mapping": "产品卖得快不快。周转慢就像菜烂在冰箱里。",
                            "accounts_needed": ["营业成本", "存货"],
                            "is_key_indicator": False,
                            "is_fatal_indicator": False,
                        },
                        {
                            "name": "固定资产周转率",
                            "formula": "营业收入 ÷ 平均固定资产",
                            "unit": "无",
                            "normal_range": ">=1.2",
                            "warning_range": "0.6~1.2",
                            "high_risk_range": "<0.6",
                            "normal_parsed": {"raw": ">=1.2", "type": "gte", "value": 1.2},
                            "warning_parsed": {"raw": "0.6~1.2", "type": "range", "low": 0.6, "high": 1.2},
                            "high_risk_parsed": {"raw": "<0.6", "type": "lt", "value": 0.6},
                            "single_mapping": "产能利用率。偏低 = 产线开工不足。",
                            "accounts_needed": ["营业收入", "固定资产"],
                            "is_key_indicator": True,
                            "is_fatal_indicator": False,
                        },
                    ],
                    "盈利能力类": [
                        {
                            "name": "销售毛利率",
                            "formula": "(营业收入 - 营业成本) ÷ 营业收入 × 100%",
                            "unit": "%",
                            "normal_range": ">=30%",
                            "warning_range": "15%~30%",
                            "high_risk_range": "<15%",
                            "normal_parsed": {"raw": ">=30%", "type": "gte", "value": 30.0},
                            "warning_parsed": {"raw": "15%~30%", "type": "range", "low": 15.0, "high": 30.0},
                            "high_risk_parsed": {"raw": "<15%", "type": "lt", "value": 15.0},
                            "single_mapping": "产品基础盈利能力。",
                            "accounts_needed": ["营业收入", "营业成本"],
                            "is_key_indicator": False,
                            "is_fatal_indicator": True,
                        },
                    ],
                },
                "linkage_rules": [
                    {
                        "combination": "毛利率飙升 + 存货周转平稳 + 合同负债高增",
                        "status": "最优状态",
                        "description": "公司量价齐升，订单饱满，赚的钱是真金白银",
                        "indicators_needed": ["销售毛利率", "存货周转率", "合同负债同比变动率"],
                    },
                    {
                        "combination": "毛利率暴跌 + 存货积压 + 现金流恶化",
                        "status": "高危状态",
                        "description": "企业周期亏损、资产减值、经营压力拉满",
                        "indicators_needed": ["销售毛利率", "存货周转率", "净利润现金含量"],
                    },
                ],
                "anomaly_rules": [
                    {
                        "id": "lithium_1",
                        "description": "锂价大跌但存货跌价损失很少",
                        "risk": "可能少计提了存货贬值，利润有水分",
                        "check": "external_lithium_price_drop > 30 AND inventory_impairment_rate < 2",
                        "requires_external": True,
                        "severity": 5,
                    },
                    {
                        "id": "lithium_2",
                        "description": "合同负债大幅高增但存货周转持续恶化",
                        "risk": "客户订单延期、弃单，订单无法落地",
                        "check": "contract_liability_growth > 50 AND inventory_turnover < 3",
                        "requires_external": False,
                        "severity": 4,
                    },
                ],
            },
            "2.1": {
                "name": "正极材料（磷酸铁锂 / 三元材料）",
                "characteristics": "加工制造属性，利润取决于加工费与原料价差",
                "focus": "应收账款、存货、产能利用率、研发费用",
                "indicators": {
                    "营运效率类": [
                        {
                            "name": "应收账款周转率",
                            "formula": "营业收入 ÷ 平均应收账款",
                            "unit": "次",
                            "normal_range": ">=4",
                            "warning_range": "2~4",
                            "high_risk_range": "<2",
                            "normal_parsed": {"raw": ">=4", "type": "gte", "value": 4.0},
                            "warning_parsed": {"raw": "2~4", "type": "range", "low": 2.0, "high": 4.0},
                            "high_risk_parsed": {"raw": "<2", "type": "lt", "value": 2.0},
                            "single_mapping": "回款速度。周转慢 = 下游客户拖欠货款。",
                            "accounts_needed": ["营业收入", "应收账款"],
                            "is_key_indicator": True,
                            "is_fatal_indicator": False,
                        },
                    ],
                },
                "linkage_rules": [
                    {
                        "combination": "营收增长 + 应收周转平稳 + 毛利率稳定",
                        "status": "良性扩张",
                        "description": "销售增长健康，回款正常，盈利能力稳定",
                        "indicators_needed": ["营收同比增速", "应收账款周转率", "销售毛利率"],
                    },
                ],
                "anomaly_rules": [
                    {
                        "id": "cathode_1",
                        "description": "营收高增但应收增速更快",
                        "risk": "放宽信用政策冲收入，坏账风险上升",
                        "check": "revenue_growth > 20 AND receivables_growth > revenue_growth * 1.5",
                        "requires_external": False,
                        "severity": 4,
                    },
                ],
            },
        },
        "general_indicators": [
            {
                "name": "销售毛利率",
                "formula": "(营业收入 - 营业成本) / 营业收入 * 100",
                "unit": "%",
                "normal_range": "跟随细分赛道基准 ±5pct",
                "warning_range": "低于基准下限 ~ 下限-8pct",
                "high_risk_range": "<基准-8pct",
                "normal_parsed": {"raw": "跟随细分赛道基准 ±5pct", "type": "raw"},
                "warning_parsed": {"raw": "低于基准下限 ~ 下限-8pct", "type": "raw"},
                "high_risk_parsed": {"raw": "<基准-8pct", "type": "raw"},
                "single_mapping": "产品基础盈利能力。毛利率下行 = 竞争加剧或成本失控。",
                "accounts_needed": ["营业收入", "营业成本"],
                "is_key_indicator": False,
                "is_fatal_indicator": False,
            },
            {
                "name": "资产负债率",
                "formula": "总负债 ÷ 总资产 × 100%",
                "unit": "%",
                "normal_range": "40%~60%",
                "warning_range": "60%~80%",
                "high_risk_range": ">80%",
                "normal_parsed": {"raw": "40%~60%", "type": "range", "low": 40.0, "high": 60.0},
                "warning_parsed": {"raw": "60%~80%", "type": "range", "low": 60.0, "high": 80.0},
                "high_risk_parsed": {"raw": ">80%", "type": "gt", "value": 80.0},
                "single_mapping": "财务杠杆水平。过高 = 偿债压力大。",
                "accounts_needed": ["总负债", "总资产"],
                "is_key_indicator": False,
                "is_fatal_indicator": False,
            },
            {
                "name": "净利润现金含量",
                "formula": "经营活动现金流净额 ÷ 当期扣非净利润",
                "unit": "%",
                "normal_range": ">=1.0",
                "warning_range": "0.5~1.0",
                "high_risk_range": "<0.5",
                "normal_parsed": {"raw": ">=1.0", "type": "gte", "value": 1.0},
                "warning_parsed": {"raw": "0.5~1.0", "type": "range", "low": 0.5, "high": 1.0},
                "high_risk_parsed": {"raw": "<0.5", "type": "lt", "value": 0.5},
                "single_mapping": "利润含金量。小于1 = 利润是纸面富贵。",
                "accounts_needed": ["经营活动现金流净额", "扣非净利润"],
                "is_key_indicator": False,
                "is_fatal_indicator": False,
            },
        ],
        "account_mapping": {
            "营业收入": ["营业收入", "营业总收入", "Revenue", "Total Revenue"],
            "营业成本": ["营业成本", "营业总成本", "Cost of Revenue"],
            "存货": ["存货", "Inventories", "Inventory"],
            "应收账款": ["应收账款", "应收票据及应收账款", "Trade Receivables"],
            "固定资产": ["固定资产", "Fixed Assets", "Property Plant and Equipment"],
            "总负债": ["总负债", "负债合计", "Total Liabilities"],
            "总资产": ["总资产", "资产总计", "Total Assets"],
            "经营活动现金流净额": ["经营活动现金流净额", "经营活动产生的现金流量净额"],
            "扣非净利润": ["扣非净利润", "扣除非经常性损益的净利润"],
            "合同负债": ["合同负债", "Contract Liabilities"],
        },
    }


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Determine which data source to use
    json_path = Path(__file__).parent / "lithium_knowledge_base.json"

    if json_path.exists():
        print(f"Loading production KB from: {json_path}")
        print(f"File size: {json_path.stat().st_size / 1024:.1f} KB\n")
        kb = LithiumKnowledgeBase(json_path).load()
    else:
        print("Production KB not found. Using mock data for demo.\n")
        kb = LithiumKnowledgeBase(generate_mock_kb()).load()

    # ── Validation report ──
    kb.print_validation_report()

    # ── Query demos ──
    print(f"\n{'='*60}")
    print("Query Demos")
    print(f"{'='*60}")

    # List sectors
    print(f"\nSectors ({len(kb.list_sectors())}): {', '.join(kb.list_sectors())}")

    # Get a sector
    sector = kb.get_sector("1.1")
    if sector:
        print(f"\nSector 1.1: {sector['name']}")
        print(f"  Focus: {sector['focus'][:80]}...")

    # Get an indicator
    ind = kb.get_indicator("1.1", "存货周转率")
    if ind:
        print(f"\nIndicator '存货周转率':")
        print(f"  Formula: {ind['formula']}")
        print(f"  Normal: {ind.get('normal_range', ind.get('normal'))}")
        print(f"  Risk: {ind.get('high_risk_range', ind.get('high_risk'))}")
        print(f"  Single mapping: {ind['single_mapping'][:60]}...")

    # Get linkage rules
    linkage = kb.get_linkage_rules("1.1")
    print(f"\nLinkage rules for 1.1: {len(linkage)}")
    for r in linkage:
        print(f"  [{r['status']}] {r['combination'][:60]}...")

    # Get anomaly rules (excluding external-data-dependent)
    anomaly = kb.get_anomaly_rules("1.1", include_external=False)
    all_anomaly = kb.get_anomaly_rules("1.1", include_external=True)
    print(f"\nAnomaly rules for 1.1: {len(anomaly)} self-contained, "
          f"{len(all_anomaly) - len(anomaly)} require external data")

    # Resolve accounts
    test_accounts = ["Revenue", "营业总收入", "存货", "UnknownThing"]
    print(f"\nAccount resolution:")
    for acct in test_accounts:
        resolved = kb.resolve_account(acct)
        print(f"  '{acct}' → {resolved}")

    # Get general indicators
    general = kb.get_general_indicators()
    print(f"\nGeneral indicators: {len(general)}")
    for gi in general:
        print(f"  {gi['name']}: {gi['formula'][:50]}...")

"""
Node 1: classify_sector — 赛道识别

Three-tier identification:
  1. Lookup table (stock_code → sector) — mocked for MVP
  2. Automatic keyword matching against primary_business
  3. Manual fallback (manual_sector) when confidence < 0.6

Output: sector_level1, sector_level2, sector_characteristics, analysis_focus, sub_sectors
"""

from typing import Dict, List, Optional, Tuple, Any

# ── Level-1 sector name mapping ───────────────────────────────────────
# Maps the first digit of sector code to the level-1 name

_LEVEL1_NAMES: Dict[int, str] = {
    1: "上游资源与锂盐",
    2: "中游四大主材+箔材辅材",
    3: "中游电芯&结构件",
    4: "下游应用&后市场",
    5: "锂电专用设备&技术服务",
    6: "一体化/跨界企业",
}


# ── Keyword library ───────────────────────────────────────────────────
# Each entry: (sector_code, level2_name, [chinese_keywords], [english_keywords])
# level2_name is a short human-readable label, not the full KB name.

_KEYWORD_LIBRARY: List[Tuple[str, str, List[str], List[str]]] = [
    # ── 上游资源与锂盐 ──
    ("1.1", "锂资源开采 & 锂盐",
     ["碳酸锂", "氢氧化锂", "锂盐", "锂辉石", "锂精矿", "锂矿开采"],
     ["lithium carbonate", "lithium hydroxide", "lithium salt", "spodumene",
      "lithium mining"]),
    ("1.2", "三元前驱体 & 钴镍盐",
     ["三元前驱体", "硫酸钴", "镍豆", "钴粉", "镍粉", "钴镍"],
     ["ternary precursor", "cobalt sulfate", "nickel matte", "cobalt powder",
      "nickel powder"]),

    # ── 中游四大主材+箔材辅材 ──
    ("2.1", "正极材料",
     ["正极", "磷酸铁锂", "三元正极", "LFP", "NCM", "NCA", "磷酸锰铁锂"],
     ["cathode", "LFP", "NCM", "NCA", "LMFP", "cathode material"]),
    ("2.2", "负极材料",
     ["负极", "人造石墨", "天然石墨", "硅碳负极", "硅基负极"],
     ["anode", "artificial graphite", "natural graphite", "silicon carbon anode",
      "Si anode"]),
    ("2.3", "电解液",
     ["电解液", "六氟磷酸锂", "溶剂", "添加剂", "LiPF6"],
     ["electrolyte", "LiPF6", "solvent", "additive", "electrolyte solution"]),
    ("2.4", "隔膜",
     ["隔膜", "湿法", "干法", "涂覆", "基膜", "PVDF"],
     ["separator", "wet process", "dry process", "coating", "base film", "PVDF"]),
    ("2.5", "铜箔/铝箔",
     ["铜箔", "铝箔", "锂电箔材", "复合铜箔"],
     ["copper foil", "aluminum foil", "battery foil", "composite copper foil"]),
    ("2.6", "导电剂/粘结剂/辅材",
     ["导电剂", "粘结剂", "极耳", "CNT", "CMC", "SBR"],
     ["conductive agent", "binder", "tab", "CNT", "CMC", "SBR"]),

    # ── 中游电芯&结构件 ──
    ("3.1", "动力电池",
     ["动力电池", "动力电芯", "车用电池", "LIB", "动力锂电池"],
     ["power battery", "EV battery", "cell", "LIB", "lithium ion battery"]),
    ("3.2", "储能电池",
     ["储能电池", "储能电芯", "大型储能", "工商业储能"],
     ["energy storage battery", "ESS cell", "large scale storage",
      "industrial storage"]),
    ("3.3", "消费电池",
     ["消费电池", "3C电池", "手机电池", "笔记本电池", "电动工具"],
     ["consumer battery", "3C battery", "mobile battery", "notebook battery",
      "power tool battery"]),
    ("3.4", "PACK/模组",
     ["PACK", "模组", "电池包", "BMS", "电池系统"],
     ["battery pack", "module", "BMS", "battery system"]),
    ("3.5", "结构件",
     ["结构件", "壳体", "顶盖", "防爆阀", "盖板"],
     ["structural part", "cell case", "top cap", "vent", "cover plate"]),

    # ── 下游应用&后市场 ──
    ("4.1", "储能系统集成",
     ["储能系统", "储能集成", "EPC", "PCS", "储能项目"],
     ["energy storage system", "ESS integration", "EPC", "PCS", "storage project"]),
    ("4.2", "电池回收",
     ["回收", "梯次利用", "材料再生", "拆解", "废旧电池", "黑粉"],
     ["recycling", "cascade utilization", "material regeneration", "dismantling",
      "black mass"]),
    ("4.3", "电池贸易/经销",
     ["电池贸易", "经销", "电池销售", "渠道"],
     ["battery trading", "distribution", "battery sales", "channel"]),

    # ── 锂电专用设备&技术服务 ──
    ("5.1", "锂电设备制造",
     ["设备", "整线", "涂布机", "卷绕机", "叠片机", "化成分容"],
     ["equipment", "production line", "coating machine", "winding machine",
      "stacking machine", "formation"]),
    ("5.2", "检测/测试",
     ["检测", "测试设备", "实验室设备", "在线检测"],
     ["testing", "inspection equipment", "lab equipment", "in-line testing"]),
    ("5.3", "运维/技术服务",
     ["运维", "技术服务", "产线调试", "工艺改造", "技术授权"],
     ["O&M", "technical service", "commissioning", "process upgrade",
      "technology licensing"]),
]


# ── Mock lookup table ─────────────────────────────────────────────────
# stock_code → sector mapping (MVP: partial; real data to come from Excel)

_MOCK_LOOKUP_TABLE: Dict[str, str] = {
    "300750": "3.1",   # 宁德时代 (stock code)
    "002466": "1.1",   # 天齐锂业
    "002460": "1.1",   # 赣锋锂业
    "002594": "6.1",   # 比亚迪 — integrated
    "300014": "3.1",   # 亿纬锂能
    "688005": "2.1",   # 容百科技
    "300274": "4.1",   # 阳光电源
    "300450": "5.1",   # 先导智能
}


# ── Integration sub-sectors for known integrated companies ────────────

_MOCK_INTEGRATED_SUB_SECTORS: Dict[str, List[str]] = {
    "002594": ["3.1", "3.2", "3.3"],  # 比亚迪: 动力+储能+消费
}


# ── Scoring ───────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for keyword matching: lowercase, strip whitespace."""
    return text.strip().lower()


def _score_sector(texts: List[str], chinese_kw: List[str],
                  english_kw: List[str]) -> Tuple[int, int]:
    """Count how many keywords from this sector appear in the given texts.

    Args:
        texts: List of business description strings (primary_business items).
        chinese_kw: Chinese keywords for this sector.
        english_kw: English keywords for this sector.

    Returns:
        (matched_count, total_keyword_count)
    """
    all_kw = chinese_kw + english_kw
    normalized_texts = [_normalize(t) for t in texts]
    combined_text = " ".join(normalized_texts)

    matched = 0
    for kw in all_kw:
        kw_norm = _normalize(kw)
        if kw_norm in combined_text:
            matched += 1
        else:
            # Also try individual text matching (avoids cross-text false negatives)
            for t in normalized_texts:
                if kw_norm in t:
                    matched += 1
                    break

    return matched, len(all_kw)


def _compute_confidence(matched: int, total: int) -> float:
    """Simple confidence: matched keywords / total keywords for the sector."""
    if total == 0:
        return 0.0
    return matched / total


# ── Main node function ────────────────────────────────────────────────

def classify_sector(
    company_name: str = "",
    stock_code: str = "",
    primary_business: Optional[List[str]] = None,
    manual_sector: Optional[str] = None,
    kb: Any = None,  # LithiumKnowledgeBase instance (optional)
) -> Dict[str, Any]:
    """Classify the company into a lithium industry sub-sector.

    Args:
        company_name: Company name (用于日志).
        stock_code: Stock code (用于查表).
        primary_business: List of business segment descriptions.
        manual_sector: User-provided sector code, e.g. "3.1" (兜底).
        kb: LithiumKnowledgeBase instance. If provided, sector characteristics
            and analysis focus are read from KB.

    Returns:
        Dict with keys matching AnalysisState sector layer:
        sector_level1, sector_level2, sector_characteristics,
        analysis_focus, sub_sectors
    """
    primary_business = primary_business or []
    matched_sectors: List[Tuple[str, float]] = []  # (code, confidence)

    # ── Tier 1: Lookup table ──
    if stock_code and stock_code in _MOCK_LOOKUP_TABLE:
        sector_code = _MOCK_LOOKUP_TABLE[stock_code]
        result = _build_result(sector_code, kb,
                               _MOCK_INTEGRATED_SUB_SECTORS.get(stock_code))
        result["_source"] = "lookup_table"
        return result

    # ── Tier 2: Keyword matching ──
    for code, level2, ch_kw, en_kw in _KEYWORD_LIBRARY:
        matched, total = _score_sector(primary_business, ch_kw, en_kw)
        if matched > 0:
            conf = _compute_confidence(matched, total)
            matched_sectors.append((code, conf))

    if matched_sectors:
        # Sort by confidence descending
        matched_sectors.sort(key=lambda x: x[1], reverse=True)

        # Check for integrated enterprise: sectors from different level-1 groups
        level1_groups = {code[0] for code, _ in matched_sectors}
        best_code, best_conf = matched_sectors[0]

        if len(level1_groups) >= 2:
            # ── Integrated enterprise ──
            sub_codes = [code for code, _ in matched_sectors]
            result = _build_result("6.1", kb, sub_sectors=sub_codes)
            result["_source"] = "keyword_matching_integrated"
            result["_confidence"] = best_conf
            return result
        else:
            # ── Single sector ──
            if best_conf >= 0.6:
                result = _build_result(best_code, kb)
                result["_source"] = "keyword_matching"
                result["_confidence"] = best_conf
                return result
            elif manual_sector:
                # Low confidence, but manual_sector provided → use manual
                pass  # fall through to Tier 3
            else:
                # Low confidence, no manual fallback → still return best guess
                result = _build_result(best_code, kb)
                result["_source"] = "keyword_matching_low_confidence"
                result["_confidence"] = best_conf
                return result

    # ── Tier 3: Manual fallback ──
    if manual_sector:
        result = _build_result(manual_sector, kb)
        result["_source"] = "manual_fallback"
        return result

    # ── No match at all ──
    result = _build_result(None, kb)
    result["_source"] = "unknown"
    return result


def _build_result(
    sector_code: Optional[str],
    kb: Any = None,
    sub_sectors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the result dict from a sector code. Fetches characteristics
    and focus from the knowledge base if available."""
    level1: Optional[str] = None
    level2: Optional[str] = None
    characteristics: Optional[str] = None
    focus: Optional[str] = None

    if sector_code:
        # Determine level-1 from first digit
        try:
            level1_key = int(sector_code.split(".")[0])
            level1 = _LEVEL1_NAMES.get(level1_key)
        except (ValueError, IndexError):
            level1 = None

        # Try KB for full sector info
        if kb and hasattr(kb, "get_sector"):
            sector = kb.get_sector(sector_code)
            if sector:
                level2 = sector.get("name", "")
                characteristics = sector.get("characteristics", "")
                focus = sector.get("focus", "")

        # Fallback: use keyword library for level2 name
        if not level2:
            for code, name, _, _ in _KEYWORD_LIBRARY:
                if code == sector_code:
                    level2 = name
                    break

    return {
        "sector_level1": level1,
        "sector_level2": level2,
        "sector_characteristics": characteristics,
        "analysis_focus": focus,
        "sub_sectors": sub_sectors,
        "_sector_code": sector_code,
    }


# ── Utility ──────────────────────────────────────────────────────────

def get_sector_keywords(sector_code: str) -> Tuple[List[str], List[str]]:
    """Return (chinese_keywords, english_keywords) for a given sector code."""
    for code, _, ch_kw, en_kw in _KEYWORD_LIBRARY:
        if code == sector_code:
            return ch_kw, en_kw
    return [], []


def list_all_sectors() -> List[str]:
    """Return all known sector codes from the keyword library."""
    return [code for code, _, _, _ in _KEYWORD_LIBRARY]

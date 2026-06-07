"""
Node 6: linkage_analysis — 联动诊断

LLM node (DeepSeek-V4-Flash). Analyzes combinations of key indicators
to produce natural-language diagnostic conclusions.

Input: structured JSON of key indicators + general indicators
Output: List[LinkageState] — indicator combinations with business interpretation
"""

import json
from typing import Dict, List, Optional, Any

from nodes.llm_client import call_llm, extract_json_from_response


# ── System Prompt ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位锂电行业财务分析师，擅长用通俗的语言解释复杂的财务数据。

请基于以下已计算的财务指标数据，判断公司当前的经营状态。

## 输入
JSON格式的指标数据，包含：
- sector_level2: 当前赛道
- key_indicators: 核心先行指标与生死指标的计算结果
- general_indicators: 通用指标计算结果

## 任务
1. 从知识库读取当前赛道的"赛道整体经营实景联动映射"规则（3~5条典型状态组合）。
2. 将实际指标与规则匹配，识别当前状态。
3. 对每条匹配到的状态，用通俗的语言解释：
   - 这些数字组合在一起说明什么（用大白话）
   - 对公司经营意味着什么
   - 投资者/分析师应该关注什么

## 输出格式
JSON数组，每个元素：
{
  "combination": "指标组合描述",
  "status": "状态等级（最优状态/良性扩张/风险累积/高危状态/衰退信号）",
  "description": "通俗解释：这些数字说明公司在经历什么（2-3句话，避免专业术语，必要时用括号解释）",
  "investment_implication": "对投资的含义（1-2句话，直白说明）",
  "evidence": {"指标名": 数值}
}

## 约束
1. 严禁编造数据，所有结论必须引用输入中的具体数值。
2. 最多返回5条，按严重程度排序（高危状态优先）。
3. 如果当前数据不匹配任何典型状态，输出"当前经营状态较为平稳，未发现典型联动模式"。
4. 语言风格：通俗科普，避免过于晦涩的行业黑话，必要时在括号里解释术语。
5. 一体化企业：如果输入包含多个子赛道的指标，分别对每个子赛道做联动诊断，输出多套结论。"""


# ── Main node function ────────────────────────────────────────────────

def linkage_analysis(
    sector_level2: Optional[str] = None,
    sub_sectors: Optional[List[str]] = None,
    key_indicators_for_linkage: Optional[Dict[str, dict]] = None,
    general_indicators: Optional[Dict[str, dict]] = None,
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, dict]]]] = None,
    sector_characteristics: Optional[str] = None,
    analysis_focus: Optional[str] = None,
    kb: Any = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run linkage diagnosis using LLM.

    Args:
        sector_level2: Current sector name.
        sub_sectors: Sub-sector codes for integrated enterprises.
        key_indicators_for_linkage: Filtered key indicators.
        general_indicators: All computed general indicators.
        sector_indicators: All computed sector indicators.
        sector_characteristics: Sector description (for context).
        analysis_focus: What to focus on (for context).
        kb: LithiumKnowledgeBase instance (for linkage rules).
        llm_config: Override LLM API config.

    Returns:
        Dict with linkage_diagnosis: List[LinkageState].
    """
    key_indicators_for_linkage = key_indicators_for_linkage or {}
    general_indicators = general_indicators or {}

    # ── Build KB context: linkage rules for this sector ──
    kb_context = ""
    if kb and hasattr(kb, "get_linkage_rules"):
        # Try to extract sector code from sector_level2
        sector_code = _extract_code_from_sector(sector_level2)
        if sector_code:
            rules = kb.get_linkage_rules(sector_code)
            if rules:
                kb_context = "\n## 该赛道的典型联动状态模式（知识库）\n"
                for i, rule in enumerate(rules, 1):
                    kb_context += f"{i}. {rule.get('combination', '')} → "
                    kb_context += f"{rule.get('status', '')}："
                    kb_context += f"{rule.get('description', '')}\n"
        # For integrated, collect rules from all sub-sectors
        if sub_sectors:
            for sub_code in sub_sectors:
                rules = kb.get_linkage_rules(sub_code)
                if rules:
                    kb_context += f"\n## 子赛道 {sub_code} 的联动状态模式\n"
                    for i, rule in enumerate(rules, 1):
                        kb_context += f"{i}. {rule.get('combination', '')} → "
                        kb_context += f"{rule.get('status', '')}："
                        kb_context += f"{rule.get('description', '')}\n"

    # ── Simplify indicators for LLM input ──
    simplified_key = _simplify_indicators(key_indicators_for_linkage)
    simplified_general = _simplify_indicators(general_indicators)

    # ── Build user message ──
    user_data = {
        "sector_level2": sector_level2 or "未知赛道",
        "sector_characteristics": sector_characteristics or "",
        "analysis_focus": analysis_focus or "",
        "key_indicators": simplified_key,
        "general_indicators": simplified_general,
    }

    user_message = json.dumps(user_data, ensure_ascii=False, indent=2)

    # Add KB context to system prompt
    full_system = SYSTEM_PROMPT
    if kb_context:
        full_system += "\n" + kb_context

    # ── Call LLM ──
    response = call_llm(
        system_prompt=full_system,
        user_message=user_message,
        config=llm_config,
    )

    # ── Parse response ──
    diagnosis: List[Dict] = []
    if response:
        parsed = extract_json_from_response(response)
        if parsed:
            diagnosis = parsed
        else:
            diagnosis = [{
                "combination": "解析失败",
                "status": "无法判定",
                "description": f"LLM返回无法解析，原始响应: {response[:200]}",
                "investment_implication": "请联系开发者检查LLM配置。",
                "evidence": {},
            }]
    else:
        diagnosis = [{
            "combination": "LLM不可用",
            "status": "无法判定",
            "description": "LLM服务未配置或调用失败（请设置 DEEPSEEK_API_KEY 环境变量）。",
            "investment_implication": "配置API密钥后重新运行。",
            "evidence": {},
        }]

    return {"linkage_diagnosis": diagnosis}


def _simplify_indicators(indicators: Dict[str, dict]) -> Dict[str, dict]:
    """Reduce indicator dict to only the fields LLM needs: value, risk_level, unit."""
    simplified = {}
    for name, ind in indicators.items():
        simplified[name] = {
            "value": ind.get("value"),
            "risk_level": str(ind.get("risk_level", "")),
            "unit": ind.get("unit", ""),
        }
    return simplified


def _extract_code_from_sector(sector_level2: Optional[str]) -> Optional[str]:
    """Try to extract sector code from sector_level2 name."""
    if not sector_level2:
        return None
    import re
    m = re.match(r'^([\d.]+)', sector_level2.strip())
    if m:
        code = m.group(1)
        if '.' in code:
            return code
    return None

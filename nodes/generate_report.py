"""
Node 8: generate_report — 报告生成

LLM node (DeepSeek-V4-Flash). Assembles all analysis results into a
structured, plain-language financial analysis report in Markdown format.

When LLM is unavailable (no API key), falls back to a deterministic
Python report generator that produces the same 6-chapter structure.
"""

import json
from typing import Dict, List, Optional, Any

from nodes.llm_client import call_llm


# ── System Prompt ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位擅长把复杂财务数据讲清楚的财经科普作者。

请基于以下数据，生成一份通俗易懂的锂电公司财报分析报告。

## 输入
JSON格式的 AnalysisState（含指标计算结果、联动诊断、异常扫描结果）。

## 强制输出结构

### 一、公司画像：它是做什么的？
- 一句话说明公司匹配的细分赛道
- 这个赛道是怎么赚钱的（用大白话解释，比如"卖锂盐就像卖面粉，价格波动大，赚的是差价"）

### 二、体检报告：四大维度速览
用表格展示通用指标（能算出来的）：
| 指标 | 数值 | 风险等级 | 通俗解读 |
|-----|------|---------|---------|
风险等级用 🟢正常 🟡警惕 🔴高风险 ⚪待补充 标注

对于"⚪待补充"的指标（如营收同比增速），说明"需要上年数据才能计算"

### 三、赛道专属深度检查
按指标类型分组展示（如"营运效率"、"盈利能力"）。
每个指标展示：
- 数值 + 风险等级
- 一句话解释：这个数值说明什么（避免术语，用比喻或生活化语言）

### 四、联动诊断：指标组合在一起看
列出3~5条联动结论，格式：
- 🔴【高危状态】XXXX + XXXX = 说明什么
- 用通俗语言解释，避免"垫资""内卷"等黑话，或出现时立即用括号解释

### 五、异常警报
逐条列出触发的异常（如有），格式：
⚠️ [异常标题]：具体表现 → 潜在风险 → 建议查什么科目
若无异常，显示"✅ 暂未发现明显异常信号"

### 六、总结（投资人视角）
站在投资人（包括股东、债权人、潜在投资者）的立场，写出一段 150-250 字的综合评价，必须涵盖：
1. **当前价值判断**：这家公司值不值得持有/买入/减仓？基于哪几个核心数据？
2. **最大风险点**：投资者最该担心什么？具体到数字和指标。
3. **最大亮点**：有什么数据支撑正面预期？
4. **下一步观察重点**：下个季报投资者应该盯哪 2-3 个指标来验证判断？
不要使用"一句话+三要点"的机械格式。用自然段落，像分析师面对面汇报一样。

## 约束
1. 严禁编造数据，所有数值必须来自输入。
2. 术语必须通俗化：遇到"速动比率""CCC""合同负债"等术语，首次出现时必须在括号里用大白话解释。
3. 数值保留两位小数，百分比标注%，天数标注"天"。
4. 对于数据缺失的指标，显示"缺少XX数据，暂时无法计算"，不得估算。
5. 对于未接入外部数据的异常规则，在报告末尾注明："本报告暂未接入锂价、行业增速等外部市场数据，部分风险规则未启用。"
6. 末尾必须附带："本分析仅基于财务报表数据，不构成投资建议。"

## 语言风格要求
- 像给非财务专业的投资者讲解，避免"虚盈实亏""纸面利润"等过于行话的表达，改用"账面赚钱但口袋没钱"等说法。
- 允许使用比喻（如"存货周转慢就像菜烂在冰箱里"）。
- 禁止空洞的宏观叙事（如"在新能源浪潮下"），必须落实到具体数字。"""


# ── Main node function ────────────────────────────────────────────────

def generate_report(
    company_name: str = "",
    stock_code: str = "",
    current_period: str = "",
    sector_level1: Optional[str] = None,
    sector_level2: Optional[str] = None,
    sector_characteristics: Optional[str] = None,
    analysis_focus: Optional[str] = None,
    sub_sectors: Optional[List[str]] = None,
    general_indicators: Optional[Dict[str, dict]] = None,
    sector_indicators: Optional[Dict[str, Dict[str, Dict[str, dict]]]] = None,
    key_indicators_for_linkage: Optional[Dict[str, dict]] = None,
    linkage_diagnosis: Optional[List[Dict]] = None,
    anomaly_signals: Optional[List[Dict]] = None,
    error_log: Optional[List[str]] = None,
    data_completeness: Optional[float] = None,
    skipped_external_rules: int = 0,
    weighted_score: Optional[Dict[str, Any]] = None,
    metric_interpretations: Optional[List[Dict[str, Any]]] = None,
    macro_insights: Optional[Dict[str, Any]] = None,
    industry_benchmark_insights: Optional[Dict[str, Any]] = None,
    risk_summary: Optional[Dict[str, Any]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a plain-language financial analysis report.

    Args:
        company_name, stock_code, current_period: Basic company info.
        sector_level1, sector_level2: Classified sector.
        sector_characteristics, analysis_focus: From KB.
        sub_sectors: For integrated enterprises.
        general_indicators: All computed general indicators.
        sector_indicators: All computed sector indicators.
        key_indicators_for_linkage: Filtered key indicators.
        linkage_diagnosis: LLM linkage conclusions.
        anomaly_signals: Anomaly scan results.
        error_log: Validation errors/warnings.
        data_completeness: Fraction of required accounts present.
        skipped_external_rules: Count of external-data rules skipped.
        llm_config: Override LLM API config.

    Returns:
        Dict with report_markdown: str — the full Markdown report.
    """
    general_indicators = general_indicators or {}
    sector_indicators = sector_indicators or {}
    linkage_diagnosis = linkage_diagnosis or []
    anomaly_signals = anomaly_signals or []
    error_log = error_log or []

    # ── Build input JSON ──
    input_data = _build_input_json(
        company_name, stock_code, current_period,
        sector_level1, sector_level2, sector_characteristics, analysis_focus,
        sub_sectors, general_indicators, sector_indicators,
        key_indicators_for_linkage, linkage_diagnosis, anomaly_signals,
        error_log, data_completeness, skipped_external_rules, weighted_score,
        metric_interpretations, macro_insights, industry_benchmark_insights, risk_summary,
    )

    user_message = json.dumps(input_data, ensure_ascii=False, indent=2)

    # Add language reminder to system prompt
    full_system = SYSTEM_PROMPT
    if sub_sectors:
        full_system += (
            f"\n\n## 特别说明：一体化企业\n"
            f"该公司被识别为一体化/跨界企业，涉及以下子赛道：{', '.join(sub_sectors)}。"
            f"请在各章节中按子赛道分别展示专属指标和联动诊断。"
        )
    if skipped_external_rules > 0:
        full_system += (
            f"\n\n注意：有 {skipped_external_rules} 条依赖外部数据的规则未启用。"
        )

    # ── Try LLM ──
    response = call_llm(
        system_prompt=full_system,
        user_message=user_message,
        config=llm_config,
    )

    # ── Use LLM response or fallback ──
    # Detect JSON (from mock or misconfigured LLM) — JSON is not a valid report
    is_json = response and response.strip().startswith(("[", "{"))
    if response and len(response.strip()) > 50 and not is_json:
        report = response.strip()
    else:
        report = _generate_fallback_report(input_data)

    return {"report_markdown": report}


# ── Input JSON builder ────────────────────────────────────────────────

def _build_input_json(
    company_name: str,
    stock_code: str,
    current_period: str,
    sector_level1: Optional[str],
    sector_level2: Optional[str],
    sector_characteristics: Optional[str],
    analysis_focus: Optional[str],
    sub_sectors: Optional[List[str]],
    general_indicators: Dict[str, dict],
    sector_indicators: Dict[str, Dict[str, Dict[str, dict]]],
    key_indicators_for_linkage: Optional[Dict[str, dict]],
    linkage_diagnosis: List[Dict],
    anomaly_signals: List[Dict],
    error_log: List[str],
    data_completeness: Optional[float],
    skipped_external_rules: int,
    weighted_score: Optional[Dict[str, Any]] = None,
    metric_interpretations: Optional[List[Dict[str, Any]]] = None,
    macro_insights: Optional[Dict[str, Any]] = None,
    industry_benchmark_insights: Optional[Dict[str, Any]] = None,
    risk_summary: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Build the structured input JSON for the LLM."""

    # Simplify general indicators
    simplified_general = {}
    for name, ind in general_indicators.items():
        simplified_general[name] = {
            "value": ind.get("value"),
            "unit": ind.get("unit", ""),
            "risk_level": _risk_str(ind.get("risk_level")),
            "normal_range": ind.get("normal_range", ""),
            "explanation": ind.get("single_mapping", "")[:120],
            "formula": ind.get("formula", "")[:80],
        }

    # Simplify sector indicators
    simplified_sector = {}
    for sector_label, categories in sector_indicators.items():
        simplified_cats = {}
        for cat_name, indicators in categories.items():
            simplified_inds = {}
            for ind_name, ind in indicators.items():
                simplified_inds[ind_name] = {
                    "value": ind.get("value"),
                    "unit": ind.get("unit", ""),
                    "risk_level": _risk_str(ind.get("risk_level")),
                    "normal_range": ind.get("normal_range", ""),
                    "explanation": ind.get("single_mapping", "")[:120],
                }
            if simplified_inds:
                simplified_cats[cat_name] = simplified_inds
        if simplified_cats:
            simplified_sector[sector_label] = simplified_cats

    # Simplify key indicators
    simplified_key = {}
    if key_indicators_for_linkage:
        for name, ind in key_indicators_for_linkage.items():
            simplified_key[name] = {
                "value": ind.get("value"),
                "unit": ind.get("unit", ""),
                "risk_level": _risk_str(ind.get("risk_level")),
            }

    # Count pending indicators
    pending_count = sum(
        1 for ind in general_indicators.values()
        if "待补充" in str(ind.get("risk_level", ""))
    )

    return {
        "company": {
            "name": company_name,
            "stock_code": stock_code,
            "period": current_period,
        },
        "sector": {
            "level1": sector_level1,
            "level2": sector_level2,
            "characteristics": (sector_characteristics or "")[:300],
            "focus": (analysis_focus or "")[:300],
            "sub_sectors": sub_sectors,
        },
        "data_quality": {
            "completeness": f"{(data_completeness or 0) * 100:.0f}%",
            "pending_indicators": pending_count,
            "errors": error_log[:10],  # truncate for prompt size
        },
        "general_indicators": simplified_general,
        "sector_indicators": simplified_sector,
        "key_indicators": simplified_key,
        "linkage_diagnosis": linkage_diagnosis[:5],
        "anomaly_signals": anomaly_signals,
        "skipped_external_rules": skipped_external_rules,
        "weighted_score": weighted_score or {},
        "metric_interpretations": (metric_interpretations or [])[:12],
        "macro_insights": macro_insights or {},
        "industry_benchmark_insights": industry_benchmark_insights or {},
        "risk_summary": risk_summary or {},
    }


def _risk_str(risk_level: Any) -> str:
    """Convert RiskLevel enum or string to emoji string."""
    if risk_level is None:
        return "⚪待补充"
    s = str(risk_level)
    if hasattr(risk_level, 'value'):
        s = risk_level.value
    return s


# ── Fallback report generator ─────────────────────────────────────────
# Used when LLM is unavailable. Produces the required 6-chapter structure.

def _generate_fallback_report(data: Dict) -> str:
    """Generate a deterministic fallback report in the required 6-chapter format."""
    lines = []
    company = data.get("company", {})
    sector = data.get("sector", {})
    dq = data.get("data_quality", {})
    general = data.get("general_indicators", {})
    sector_inds = data.get("sector_indicators", {})
    linkage = data.get("linkage_diagnosis", [])
    anomalies = data.get("anomaly_signals", [])
    skipped = data.get("skipped_external_rules", 0)
    weighted_score = data.get("weighted_score", {})

    cname = company.get("name", "未知公司")
    period = company.get("period", "最新报告期")
    s_level1 = sector.get("level1", "未分类")
    s_level2 = sector.get("level2", "")
    s_chars = sector.get("characteristics", "")
    s_focus = sector.get("focus", "")

    # ── Chapter 1: Company portrait ──
    lines.append(f"# {cname} 财报分析报告")
    lines.append(f"> 报告期：{period} | 行业定位：{s_level1}")
    lines.append("")
    lines.append("## 一、公司画像：它是做什么的？")
    lines.append("")
    lines.append(f"该公司被归类为 **{s_level2 or s_level1}**。")
    lines.append("")

    # Extract a simple business description from characteristics
    if s_chars:
        first_line = s_chars.split("\n")[0].strip()
        # Remove leading number prefix like "1. "
        first_line = first_line.lstrip("0123456789. ")
        lines.append(f"这个赛道的特点：{first_line}")
        lines.append("")

    if s_focus:
        lines.append(f"分析关注重点：{s_focus[:200]}")
        lines.append("")

    # ── Chapter 2: General indicators table ──
    lines.append("## 二、体检报告：四大维度速览")
    lines.append("")
    lines.append("| 指标 | 数值 | 风险等级 | 一句话解读 |")
    lines.append("|------|------|---------|-----------|")

    # Group by dimension
    dims = {
        "盈利能力": ["销售毛利率", "扣非销售净利率", "扣非ROE", "期间费用率"],
        "现金流质量": ["净利润现金含量"],
        "偿债能力": ["资产负债率", "流动比率", "速动比率"],
        "成长性": ["营收同比增速", "应收账款同比增速", "存货同比增速"],
    }

    for dim, names in dims.items():
        for name in names:
            ind = general.get(name, {})
            val = ind.get("value")
            risk = ind.get("risk_level", "⚪待补充")
            expl = ind.get("explanation", "")

            if val is not None:
                unit = ind.get("unit", "")
                val_str = f"{val:.2f}{unit}"
            else:
                val_str = "—"
                if "待补充" in risk:
                    val_str = "缺少上期数据，暂时无法计算"

            expl_short = expl[:60] + "..." if len(expl) > 60 else expl
            lines.append(f"| {name} | {val_str} | {risk} | {expl_short} |")

    lines.append("")
    pending = dq.get("pending_indicators", 0)
    if pending > 0:
        lines.append(f"> ⚪ 有 {pending} 项指标因缺少上期数据，暂时无法计算。")
    if weighted_score.get("score") is not None:
        lines.append(
            f"> 综合评分：**{weighted_score.get('score')} / 100**"
            f"（可用权重 {weighted_score.get('available_weight', 0)}%，"
            f"跳过缺失权重 {weighted_score.get('skipped_weight', 0)}%）。"
        )
    lines.append("")

    # ── Chapter 3: Sector-specific indicators ──
    lines.append("## 三、赛道专属深度检查")
    lines.append("")

    if sector_inds:
        for sec_label, categories in sector_inds.items():
            if len(sector_inds) > 1:
                lines.append(f"### {sec_label}")
                lines.append("")
            for cat_name, indicators in categories.items():
                lines.append(f"#### {cat_name}")
                lines.append("")
                for ind_name, ind in indicators.items():
                    val = ind.get("value")
                    risk = ind.get("risk_level", "⚪待补充")
                    unit = ind.get("unit", "")
                    expl = ind.get("explanation", "")

                    if val is not None:
                        lines.append(f"- **{ind_name}**：{val:.2f}{unit} {risk}")
                        if expl:
                            lines.append(f"  > {expl[:150]}")
                    else:
                        lines.append(f"- **{ind_name}**：{risk}（数据缺失，暂时无法计算）")
                    lines.append("")
    else:
        lines.append("暂无赛道专属指标数据。")
        lines.append("")

    # ── Chapter 4: Linkage diagnosis ──
    lines.append("## 四、联动诊断：指标组合在一起看")
    lines.append("")

    if linkage:
        status_emoji = {
            "最优状态": "🟢", "良性扩张": "🟢",
            "风险累积": "🟡", "高危状态": "🔴",
            "衰退信号": "🔴", "无法判定": "⚪",
        }
        for d in linkage[:5]:
            status = d.get("status", "无法判定")
            emoji = status_emoji.get(status, "⚪")
            comb = d.get("combination", "")
            desc = d.get("description", "")
            impl = d.get("investment_implication", "")

            lines.append(f"- {emoji}【{status}】**{comb}**")
            lines.append(f"  {desc}")
            if impl:
                lines.append(f"  > {impl}")
            lines.append("")
    else:
        lines.append("暂无联动诊断数据。")
        lines.append("")

    # ── Chapter 5: Anomaly alerts ──
    lines.append("## 五、异常警报")
    lines.append("")

    if anomalies:
        for sig in anomalies:
            rid = sig.get("rule_id", "")
            desc = sig.get("description", "")
            risk = sig.get("potential_risk", "")
            accounts = sig.get("suggested_accounts", [])
            sev = sig.get("severity", 0)

            sev_bar = "🔴" * min(sev, 5) + "⚪" * max(0, 5 - sev)
            lines.append(f"⚠️ **{rid}** {sev_bar}")
            lines.append(f"- 表现：{desc}")
            lines.append(f"- 风险：{risk}")
            if accounts:
                lines.append(f"- 建议核查科目：{'、'.join(accounts)}")
            lines.append("")
    else:
        lines.append("✅ 暂未发现明显异常信号。")
        lines.append("")

    # ── Chapter 6: Summary (investor perspective) ──
    lines.append("## 六、总结")
    lines.append("")

    summary_paragraphs = _generate_investor_summary(general, sector_inds, linkage, anomalies, skipped, weighted_score)
    for para in summary_paragraphs:
        lines.append(para)
        lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append("")
    if skipped > 0:
        lines.append(f"> ⚠️ 本报告暂未接入锂价、行业增速等外部市场数据，{skipped} 条风险规则未启用。")
        lines.append("")
    lines.append("*本分析仅基于财务报表数据，不构成投资建议。*")

    return "\n".join(lines)


def _generate_summary_line(
    general: Dict,
    sector_inds: Dict,
    linkage: List,
    anomalies: List,
) -> str:
    """Generate a one-sentence summary from indicators."""
    margin = _safe_val(general, "销售毛利率")
    debt = _safe_val(general, "资产负债率")
    cash = _safe_val(general, "净利润现金含量")
    roe = _safe_val(general, "扣非ROE")

    parts = []
    if margin is not None:
        if margin >= 20:
            parts.append(f"毛利率{margin:.1f}%处于健康水平")
        elif margin >= 10:
            parts.append(f"毛利率{margin:.1f}%偏低")
        else:
            parts.append(f"毛利率{margin:.1f}%承压严重")

    if cash is not None:
        if cash >= 1.0:
            parts.append("现金流充裕（利润含金量高）")
        elif cash >= 0.7:
            parts.append("现金流偏紧")
        else:
            parts.append("现金流紧张，账面利润落地困难")

    if anomalies:
        parts.append(f"检测到{len(anomalies)}条异常信号需关注")
    elif linkage:
        high_risk_count = sum(1 for d in linkage if "高危" in d.get("status", ""))
        if high_risk_count == 0:
            parts.append("经营状态整体平稳")
        else:
            parts.append(f"存在{high_risk_count}项高危联动模式")

    if not parts:
        return "当前数据不足以做出明确判断，建议补充关键指标后重新分析。"

    return "；".join(parts) + "。"


def _generate_key_points(
    general: Dict,
    linkage: List,
    anomalies: List,
    skipped: int,
) -> List[str]:
    """Generate 3 key takeaways."""
    points = []

    # Point 1: Worst risk indicator
    margin = _safe_val(general, "销售毛利率")
    debt = _safe_val(general, "资产负债率")
    cash = _safe_val(general, "净利润现金含量")

    if cash is not None and cash < 0.7:
        points.append(f"净利润现金含量仅{cash:.1f}，账面利润的含金量偏低，需关注应收账款和存货的周转情况。")
    elif debt is not None and debt > 70:
        points.append(f"资产负债率高达{debt:.1f}%，偿债压力较大，建议评估未来12个月的债务到期情况。")
    elif margin is not None and margin < 15:
        points.append(f"销售毛利率{margin:.1f}%低于行业健康水平，需要关注产品竞争力和成本控制能力。")
    else:
        points.append("核心财务指标处于正常或关注区间，暂未发现明显风险点。")

    # Point 2: Best performing area
    if cash is not None and cash >= 1.0:
        points.append(f"现金流质量良好（净利润现金含量{cash:.1f}），利润有真金白银支撑，抗周期能力较强。")
    elif margin is not None and margin >= 20:
        points.append(f"盈利能力较强（毛利率{margin:.1f}%），产品定价权稳固，利润空间充裕。")
    else:
        points.append("建议重点补充上期数据以计算同比增速，判断业务的增长趋势和周转健康度。")

    # Point 3: Actionable
    if anomalies:
        top = anomalies[0]
        points.append(f"重点关注「{top.get('rule_id','')}」异常：{top.get('potential_risk','')[:80]}，建议核查{', '.join(top.get('suggested_accounts',[])[:3])}。")
    elif linkage:
        worst = linkage[0]
        points.append(f"联动诊断发现「{worst.get('combination','')[:40]}」模式，{worst.get('investment_implication','')[:80]}")
    elif skipped > 0:
        points.append(f"接入锂价、行业增速等外部数据后（当前{skipped}条规则未启用），可获得更全面的风险评估。")
    else:
        points.append("建议持续跟踪下一报告期的数据变化，尤其关注合同负债、存货结构等先行指标。")

    return points


def _generate_investor_summary(
    general: Dict,
    sector_inds: Dict,
    linkage: List,
    anomalies: List,
    skipped: int,
    weighted_score: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Generate investor-perspective summary paragraphs."""
    margin = _safe_val(general, "销售毛利率")
    debt = _safe_val(general, "资产负债率")
    cash = _safe_val(general, "净利润现金含量")
    roe = _safe_val(general, "扣非ROE")
    current_ratio = _safe_val(general, "流动比率")
    score = (weighted_score or {}).get("score")

    paragraphs = []

    # Value judgment
    value_parts = []
    if score is not None:
        if score >= 75:
            value_parts.append(f"综合评分{score}/100，整体财务健康度良好")
        elif score >= 50:
            value_parts.append(f"综合评分{score}/100，财务状况中等偏下")
        else:
            value_parts.append(f"综合评分仅{score}/100，财务状况需高度警惕")

    if roe is not None:
        if roe >= 15:
            value_parts.append(f"扣非ROE达{roe:.1f}%，股东回报能力突出")
        elif roe >= 8:
            value_parts.append(f"扣非ROE为{roe:.1f}%，股东回报尚可")
        else:
            value_parts.append(f"扣非ROE仅{roe:.1f}%，股东回报偏弱")

    if margin is not None and cash is not None:
        if margin >= 20 and cash >= 1.0:
            value_parts.append("毛利率和现金含量双优，利润质量扎实")
        elif margin < 15:
            value_parts.append(f"毛利率仅{margin:.1f}%，盈利能力承压")

    if value_parts:
        paragraphs.append("**当前价值判断**：" + "；".join(value_parts) + "。")

    # Biggest risk
    risk_parts = []
    if anomalies:
        top = anomalies[0]
        risk_parts.append(f"检测到{len(anomalies)}条异常信号，其中「{top.get('rule_id','')}」最值得警惕——{top.get('potential_risk','')[:80]}")
    if debt is not None and debt > 65:
        risk_parts.append(f"资产负债率高达{debt:.1f}%，偿债压力显著")
    if cash is not None and cash < 0.7:
        risk_parts.append(f"净利润现金含量仅{cash:.1f}，账面利润缺乏现金流支撑")
    high_risk_linkage = [d for d in linkage if "高危" in d.get("status", "")]
    if high_risk_linkage:
        risk_parts.append(f"联动诊断发现{len(high_risk_linkage)}项高危模式")
    if not risk_parts:
        risk_parts.append("暂未发现重大风险信号")
    paragraphs.append("**最大风险点**：" + "；".join(risk_parts) + "。")

    # Biggest highlight
    highlight_parts = []
    if cash is not None and cash >= 1.2:
        highlight_parts.append(f"现金含量{cash:.1f}，利润含金量极高")
    if margin is not None and margin >= 25:
        highlight_parts.append(f"毛利率{margin:.1f}%，产品竞争力强")
    if current_ratio is not None and current_ratio >= 1.5:
        highlight_parts.append(f"流动比率{current_ratio:.1f}，短期偿债能力充裕")
    if debt is not None and debt < 40:
        highlight_parts.append(f"资产负债率仅{debt:.1f}%，财务结构稳健")
    if not highlight_parts:
        highlight_parts.append("建议补充更多数据以发掘潜在亮点")
    paragraphs.append("**最大亮点**：" + "；".join(highlight_parts) + "。")

    # What to watch next
    watch = ["合同负债变化（反映订单景气度）", "存货结构与周转天数（库存风险前瞻）"]
    if cash is not None and cash < 1.0:
        watch.insert(0, "经营性现金流是否改善")
    if debt is not None and debt > 60:
        watch.insert(0, "短期借款和长期借款到期节奏")
    paragraphs.append("**下一步观察重点**：下个季报重点关注" + "、".join(watch[:3]) + "。")

    if skipped > 0:
        paragraphs.append(f"> 注意：当前{skipped}条依赖外部市场数据（锂价、行业增速）的风险规则未启用，接入后评估将更完整。")

    return paragraphs


def _safe_val(indicators: Dict, name: str) -> Optional[float]:
    """Safely get an indicator value."""
    ind = indicators.get(name, {})
    if isinstance(ind, dict):
        return ind.get("value")
    return None

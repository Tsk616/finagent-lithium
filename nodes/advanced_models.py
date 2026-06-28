"""
Advanced financial analysis models (报告附加四大板块).

Eight classic financial-analysis models grouped into four sections:
  business   — 杜邦分析 (DuPont) + 本量利 (CVP)
  risk       — 现金流健康度 + Z-score 破产预警
  valuation  — DCF 折现估值 + 相对估值 (PE/PB/PS/PEG + 夏普)
  strategy   — 哈佛分析框架 (LLM) + EVA 经济增加值

All numeric models are pure deterministic Python. Only the Harvard framework
uses the LLM (qualitative judgment). Each model degrades gracefully: missing
inputs yield ⚪待补充 indicators rather than raising.

Indicator results follow the IndicatorResult convention used elsewhere
(name/value/unit/formula/risk_level...) plus a frontend-ready risk_color.
Amounts are reported in 亿元; ratios in 倍/次; rates in %.
"""

from typing import Any, Dict, List, Optional

from nodes.calculate_general import judge_risk_level, RiskLevel

# ── Assumption defaults (锂电行业近似；章节内需明示) ──────────────────────
DEFAULT_WACC = 0.09
DEFAULT_PERPETUAL_GROWTH = 0.025
DEFAULT_RISK_FREE = 0.02
DEFAULT_TAX_RATE = 0.25
DEFAULT_FCF_GROWTH = 0.08


# ── Value helpers ────────────────────────────────────────────────────────

def _v(data: Dict[str, Any], *keys: str) -> Optional[float]:
    """First exact-match non-None value among alias keys."""
    for k in keys:
        val = data.get(k)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def _sum_opt(*vals: Optional[float]) -> Optional[float]:
    """Sum present (non-None) values; None if all missing."""
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


def _ratio(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    r = _ratio(a, b)
    return r * 100 if r is not None else None


def _yi(x: Optional[float]) -> Optional[float]:
    """元 → 亿元."""
    return x / 1e8 if x is not None else None


def _r(x: Optional[float], nd: int = 2) -> Optional[float]:
    return round(x, nd) if x is not None else None


def _risk_level_text(rl: Optional[RiskLevel]) -> str:
    """Display value of a RiskLevel. Note: RiskLevel is a (str, Enum); str(rl)
    yields 'RiskLevel.NORMAL', so use .value to get the '🟢正常' label."""
    if rl is None:
        return ""
    return rl.value if isinstance(rl, RiskLevel) else str(rl)


def _risk_color(rl: Optional[RiskLevel]) -> str:
    s = _risk_level_text(rl)
    if "正常" in s:
        return "normal"
    if "警惕" in s:
        return "warning"
    if "高风险" in s:
        return "danger"
    if "待补充" in s:
        return "pending"
    return "info"


def _fmt_display(value: Optional[float], unit: str) -> str:
    if value is None:
        return "—"
    if unit == "亿元":
        return f"{value:.2f}亿元"
    if unit == "%":
        return f"{value:.2f}%"
    if unit == "元":
        return f"{value:.2f}元"
    if unit:
        return f"{value:.2f}{unit}"
    return f"{value:.2f}"


def _ind(name: str, value: Optional[float], unit: str, formula: str,
         explanation: str, normal: str = "", warning: str = "",
         high_risk: str = "") -> Dict[str, Any]:
    """Build a frontend-ready indicator dict. Risk judged only if thresholds given."""
    if value is None:
        rl: Optional[RiskLevel] = RiskLevel.PENDING
        rng = "数据缺失"
    elif normal or high_risk:
        rl, rng = judge_risk_level(value, normal, warning, high_risk)
    else:
        rl, rng = None, ""
    return {
        "name": name,
        "value": _r(value),
        "display": _fmt_display(value, unit),
        "unit": unit,
        "formula": formula,
        "explanation": explanation,
        "risk_level": _risk_level_text(rl),
        "risk_color": _risk_color(rl),
        "range": rng,
    }


def _infer_growth(historical: Optional[Dict[str, Any]]) -> float:
    """Derive FCF growth from historical CAGR (净利润 preferred, else 营收)."""
    cagr = (historical or {}).get("cagr") or {}
    for key in ("净利润", "营业收入"):
        item = cagr.get(key)
        if isinstance(item, dict) and item.get("value") is not None:
            try:
                g = float(item["value"])
            except (ValueError, TypeError):
                continue
            g = g / 100 if abs(g) > 1 else g  # accept either % or fraction
            return max(0.0, min(g, 0.30))
    return DEFAULT_FCF_GROWTH


# ── Equity / debt aliases ────────────────────────────────────────────────

_EQUITY_KEYS = ("净资产", "股东权益", "所有者权益合计", "归属于母公司股东权益",
                "归属于母公司所有者权益", "股东权益合计")
_INTEREST_DEBT_KEYS = ("短期借款", "长期借款", "一年内到期的非流动负债",
                       "一年内到期非流动负债", "应付债券")


def _interest_bearing_debt(data: Dict[str, Any]) -> Optional[float]:
    return _sum_opt(*[_v(data, k) for k in _INTEREST_DEBT_KEYS])


# ── 1. DuPont (杜邦分析) ──────────────────────────────────────────────────

def _ebit(data: Dict[str, Any]) -> Optional[float]:
    """EBIT ≈ 利润总额 + 财务费用 (financial expense as interest proxy)."""
    ebt = _v(data, "利润总额")
    if ebt is None:
        return None
    return ebt + (_v(data, "财务费用") or 0)


def calculate_dupont(data: Dict[str, Any],
                     historical: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rev = _v(data, "营业收入", "营业总收入")
    cogs = _v(data, "营业成本")
    op = _v(data, "营业利润")
    ni = _v(data, "净利润", "归属于母公司股东的净利润", "归母净利润")
    ta = _v(data, "总资产", "资产总计")
    eq = _v(data, *_EQUITY_KEYS)
    ebit = _ebit(data)

    gross_margin = _pct(rev - cogs, rev) if (rev and cogs is not None) else None
    op_margin = _pct(op, rev)
    net_margin = _pct(ni, rev)
    asset_turnover = _ratio(rev, ta)
    equity_mult = _ratio(ta, eq)
    roa = _pct(ni, ta)
    roe = _pct(ni, eq)
    lever_contrib = (roe - roa) if (roe is not None and roa is not None) else None

    indicators = [
        _ind("销售毛利率", gross_margin, "%", "(营业收入−营业成本)/营业收入",
             "盈利梯队①：产品的初始盈利空间", ">=20%", "10%~20%", "<10%"),
        _ind("营业利润率", op_margin, "%", "营业利润/营业收入",
             "盈利梯队②：主营经营的盈利能力", ">=8%", "3%~8%", "<3%"),
        _ind("销售净利率", net_margin, "%", "净利润/营业收入",
             "盈利梯队③：最终落袋的净利润率", ">=5%", "2%~5%", "<2%"),
        _ind("总资产周转率", asset_turnover, "次", "营业收入/总资产",
             "营运效率：单位资产的创收能力", ">=0.7", "0.4~0.7", "<0.4"),
        _ind("权益乘数", equity_mult, "倍", "总资产/净资产",
             "财务杠杆：负债对净资产的放大", "1~2.5", "2.5~4", ">4"),
        _ind("总资产报酬率ROA", roa, "%", "净利润/总资产",
             "剔除杠杆后的资产真实回报", ">=5%", "2%~5%", "<2%"),
        _ind("净资产收益率ROE", roe, "%", "净利率×总资产周转率×权益乘数",
             "杜邦综合：股东资本回报率", ">=10%", "5%~10%", "<5%"),
        _ind("杠杆贡献度", lever_contrib, "%", "ROE−ROA",
             "财务杠杆为股东额外放大的回报", ">0", "", "<0"),
    ]

    # 五因子分解：ROE = 税收负担 × 利息负担 × EBIT利润率 × 周转率 × 权益乘数
    tax_burden = _ratio(ni, _v(data, "利润总额"))
    interest_burden = _ratio(_v(data, "利润总额"), ebit)
    ebit_margin = _pct(ebit, rev)
    five_factor = {
        "available": all(x is not None for x in
                         (tax_burden, interest_burden, ebit_margin, asset_turnover, equity_mult)),
        "factors": [
            {"name": "税收负担率", "value": _r(tax_burden), "unit": "", "formula": "净利润/利润总额", "hint": "越接近1税负越轻"},
            {"name": "利息负担率", "value": _r(interest_burden), "unit": "", "formula": "利润总额/EBIT", "hint": "越接近1利息侵蚀越小"},
            {"name": "EBIT利润率", "value": _r(ebit_margin), "unit": "%", "formula": "EBIT/营业收入", "hint": "纯经营盈利能力"},
            {"name": "总资产周转率", "value": _r(asset_turnover), "unit": "次", "formula": "营业收入/总资产", "hint": "资产运营效率"},
            {"name": "权益乘数", "value": _r(equity_mult), "unit": "倍", "formula": "总资产/净资产", "hint": "财务杠杆水平"},
        ],
    }

    driver = _dupont_driver(net_margin, asset_turnover, equity_mult)
    trend = _dupont_trend(historical, net_margin, asset_turnover, equity_mult, roe)
    diagnosis = _dupont_diagnosis(roe, roa, gross_margin, net_margin, driver, lever_contrib)

    return {
        "available": roe is not None or net_margin is not None,
        "indicators": indicators,
        "five_factor": five_factor,
        "driver_type": driver,
        "trend": trend,
        "chart": {
            "roe": _r(roe), "net_margin": _r(net_margin),
            "asset_turnover": _r(asset_turnover), "equity_multiplier": _r(equity_mult),
        },
        "diagnosis": diagnosis,
        "summary": (f"ROE {roe:.1f}%、ROA {roa:.1f}%，{driver[0] if driver else '驱动结构待判定'}。"
                    if (roe is not None and roa is not None) else "杜邦分解核心科目缺失。"),
    }


def _dupont_driver(nm, at, em):
    """Classify dominant ROE driver. Inputs: net_margin(%), turnover(x), multiplier(x)."""
    if None in (nm, at, em):
        return None
    sp, st, sl = (nm or 0) / 15.0, (at or 0) / 0.8, (em or 0) / 2.0
    m = max(sp, st, sl)
    if m == sp:
        return ("高利润型", "盈利能力是 ROE 主引擎（近似品牌消费/高端制造）：优势在定价权，风险在价格战侵蚀利润率。")
    if m == st:
        return ("高周转型", "资产运营效率是 ROE 主引擎（近似零售/快消）：优势在规模与供应链周转，风险在毛利偏薄。")
    return ("高杠杆型", "财务杠杆是 ROE 主引擎（近似地产/金融）：回报被负债放大，需警惕偿债与利率风险。")


def _dupont_trend(historical, nm, at, em, roe):
    """连环替代法归因 ROE 同比变动。需历史营收/净利润/总资产/净资产。"""
    out = {"available": False, "text": "", "main_driver": ""}
    if not historical or None in (nm, at, em, roe):
        return out
    metrics = historical.get("metrics", {}) or {}

    def prev(*keys):
        for k in keys:
            vals = metrics.get(k)
            if isinstance(vals, list) and len(vals) >= 2 and vals[-2] is not None:
                return vals[-2]
        return None

    rev0, ni0, ta0 = prev("营业收入"), prev("净利润"), prev("总资产")
    eq0 = prev("净资产", "股东权益", "归属于母公司股东权益")
    if None in (rev0, ni0, ta0, eq0) or 0 in (rev0, ta0, eq0):
        return out

    nm0, at0, em0 = ni0 / rev0, rev0 / ta0, ta0 / eq0
    nm1 = nm / 100  # nm passed as percent
    roe0 = nm0 * at0 * em0 * 100
    d_nm = (nm1 - nm0) * at0 * em0 * 100
    d_at = nm1 * (at - at0) * em0 * 100
    d_em = nm1 * at * (em - em0) * 100
    contribs = {"净利率": d_nm, "周转率": d_at, "权益乘数": d_em}
    main = max(contribs, key=lambda k: abs(contribs[k]))
    delta = roe - roe0
    out.update({
        "available": True, "main_driver": main,
        "text": (f"ROE 同比{'提升' if delta >= 0 else '下滑'} {abs(delta):.1f} 个百分点，主要由{main}变动驱动"
                 f"（净利率 {d_nm:+.1f}、周转率 {d_at:+.1f}、权益乘数 {d_em:+.1f}）。"),
    })
    return out


def _dupont_diagnosis(roe, roa, gm, nm, driver, lever):
    if roe is None:
        return "核心科目缺失，无法完成杜邦诊断。"
    level = "优秀" if roe >= 15 else "良好" if roe >= 10 else "中等" if roe >= 5 else "偏弱"
    parts = [f"ROE {roe:.1f}%，处于{level}水平。"]
    if driver:
        parts.append(driver[1])
    if gm is not None and nm is not None and gm > 0:
        retention = nm / gm * 100
        parts.append(f"从毛利率 {gm:.1f}% 到净利率 {nm:.1f}%，约 {retention:.0f}% 的毛利最终转化为净利，"
                     f"{'费用与税负控制良好' if retention >= 40 else '期间费用/税负侵蚀较明显'}。")
    if lever is not None:
        parts.append(f"财务杠杆为股东{'额外贡献' if lever >= 0 else '拖累'} {abs(lever):.1f} 个百分点回报，"
                     f"{'杠杆偏高需关注偿债' if lever > 8 else '杠杆运用稳健' if lever >= 0 else '（资产回报低于负债成本）'}。")
    return "".join(parts)


# ── 2. Cost-Volume-Profit (本量利) ───────────────────────────────────────

def calculate_cvp(data: Dict[str, Any]) -> Dict[str, Any]:
    rev = _v(data, "营业收入", "营业总收入")
    cogs = _v(data, "营业成本")
    sell = _v(data, "销售费用")
    admin = _v(data, "管理费用")
    rd = _v(data, "研发费用")
    fin = _v(data, "财务费用")
    op = _v(data, "营业利润")

    variable_cost = _sum_opt(cogs, sell)
    fixed_cost = _sum_opt(admin, rd, fin)

    cm = cm_ratio = vc_ratio = bep = bep_rate = mos_amt = mos = dol = None
    if rev and variable_cost is not None and rev != 0:
        cm = rev - variable_cost
        cm_ratio = cm / rev * 100
        vc_ratio = variable_cost / rev * 100
        if fixed_cost is not None and cm > 0:
            bep = fixed_cost / (cm / rev)
            bep_rate = bep / rev * 100 if rev > 0 else None
            mos_amt = rev - bep
            mos = mos_amt / rev * 100 if rev > 0 else None
        op_eff = op if op is not None else (cm - fixed_cost if fixed_cost is not None else None)
        if cm is not None and op_eff not in (None, 0):
            dol = cm / op_eff

    indicators = [
        _ind("边际贡献额", _yi(cm), "亿元", "营业收入 − 变动成本", "可用于覆盖固定成本的额度"),
        _ind("边际贡献率", cm_ratio, "%", "边际贡献 / 营业收入",
             "每元收入贡献的边际利润", ">=30%", "15%~30%", "<15%"),
        _ind("变动成本率", vc_ratio, "%", "变动成本 / 营业收入",
             "随产量变动的成本占比", "<=70%", "70%~85%", ">85%"),
        _ind("盈亏平衡收入", _yi(bep), "亿元", "固定成本 / 边际贡献率",
             "覆盖全部成本所需的最低营收"),
        _ind("盈亏平衡作业率", bep_rate, "%", "盈亏平衡收入 / 实际营收",
             "需用多大产能才能保本（越低越安全）", "<=70%", "70%~90%", ">90%"),
        _ind("安全边际额", _yi(mos_amt), "亿元", "营业收入 − 盈亏平衡收入", "营收下滑的绝对缓冲"),
        _ind("安全边际率", mos, "%", "安全边际额 / 营业收入",
             "营收下滑的承受空间", ">=30%", "10%~30%", "<10%"),
        _ind("经营杠杆DOL", dol, "倍", "边际贡献 / 营业利润",
             "营收变动对营业利润的放大倍数", "1~3", "3~5", ">5"),
    ]

    # 成本结构拆解
    cost_structure = {
        "available": variable_cost is not None and fixed_cost is not None and rev not in (None, 0),
        "rows": [
            {"name": "变动成本", "amount": _yi(variable_cost), "ratio": _r(vc_ratio)},
            {"name": "固定成本", "amount": _yi(fixed_cost),
             "ratio": _r(fixed_cost / rev * 100) if (fixed_cost is not None and rev) else None},
        ],
    }

    # 敏感性：收入 ±10% → 营业利润变动（DOL 放大）
    sensitivity = {"available": dol is not None,
                   "up": _r(10 * dol) if dol is not None else None,
                   "down": _r(-10 * dol) if dol is not None else None}

    diagnosis = _cvp_diagnosis(cm_ratio, mos, dol, bep_rate)

    return {
        "available": cm_ratio is not None,
        "indicators": indicators,
        "cost_structure": cost_structure,
        "sensitivity": sensitivity,
        "chart": {
            "unit": "亿元",
            "fixed_cost": _yi(fixed_cost),
            "vc_ratio": _r(variable_cost / rev) if (variable_cost is not None and rev) else None,
            "sales": _yi(rev),
            "bep": _yi(bep),
        },
        "note": "固定/变动成本为会计近似拆分：变动成本≈营业成本+销售费用，固定成本≈管理+研发+财务费用。",
        "diagnosis": diagnosis,
        "summary": (f"盈亏平衡收入约 {_yi(bep):.1f} 亿元，安全边际 {mos:.0f}%。"
                    if (bep is not None and mos is not None) else "本量利所需成本科目不足。"),
    }


def _cvp_diagnosis(cm_ratio, mos, dol, bep_rate):
    if cm_ratio is None:
        return "成本科目不足，无法完成本量利诊断。"
    parts = []
    if cm_ratio >= 30:
        parts.append(f"边际贡献率 {cm_ratio:.1f}%，每元收入贡献较厚，规模效应一旦放量利润弹性大。")
    else:
        parts.append(f"边际贡献率 {cm_ratio:.1f}% 偏薄，利润高度依赖走量，单位让利空间有限。")
    if mos is not None:
        safe = "充足" if mos >= 30 else "尚可" if mos >= 10 else "薄弱"
        parts.append(f"安全边际 {mos:.0f}%（{safe}）：营收需下滑 {mos:.0f}% 才触及盈亏平衡。")
    if dol is not None:
        if dol > 5:
            parts.append(f"经营杠杆高达 {dol:.1f} 倍，营收每波动 1% 营业利润放大 {dol:.1f}%，景气向上弹性大、向下风险也大。")
        elif dol >= 3:
            parts.append(f"经营杠杆 {dol:.1f} 倍，盈利对营收变动较敏感。")
        else:
            parts.append(f"经营杠杆 {dol:.1f} 倍，盈利稳定性较好。")
    return "".join(parts)


# ── 3. Cash-flow health (现金流健康度) ───────────────────────────────────

def calculate_cashflow_health(data: Dict[str, Any]) -> Dict[str, Any]:
    ocf = _v(data, "经营活动现金流净额", "经营活动产生的现金流量净额")
    icf = _v(data, "投资活动现金流净额", "投资活动产生的现金流量净额")
    ffcf = _v(data, "筹资活动现金流净额", "筹资活动产生的现金流量净额")
    ni = _v(data, "净利润")
    rev = _v(data, "营业收入", "营业总收入")
    capex = _v(data, "购建固定资产支付的现金",
               "购建固定资产、无形资产和其他长期资产支付的现金")
    cl = _v(data, "流动负债", "流动负债合计")

    free_cf = (ocf - capex) if (ocf is not None and capex is not None) else None
    net_cf = _sum_opt(ocf, icf, ffcf)
    # 现金满足投资比：经营现金对投资活动现金流出的覆盖
    invest_cover = (ocf / abs(icf)) if (ocf is not None and icf not in (None, 0)) else None

    indicators = [
        _ind("净现比", _ratio(ocf, ni), "倍", "经营现金流净额 / 净利润",
             "利润的现金含量", ">=1", "0.7~1", "<0.7"),
        _ind("销售现金比率", _pct(ocf, rev), "%", "经营现金流净额 / 营业收入",
             "每元收入回笼的现金", ">=10%", "5%~10%", "<5%"),
        _ind("现金流量比率", _ratio(ocf, cl), "倍", "经营现金流净额 / 流动负债",
             "短期偿债的现金保障", ">=0.4", "0.2~0.4", "<0.2"),
        _ind("现金满足投资比", invest_cover, "倍", "经营现金流净额 / 投资现金流出",
             "自身造血对扩张的支撑度", ">=1", "0.5~1", "<0.5"),
        _ind("自由现金流", _yi(free_cf), "亿元", "经营现金流净额 − 资本支出",
             "可自由支配的现金", ">0", "", "<=0"),
        _ind("自由现金流利润率", _pct(free_cf, rev), "%", "自由现金流 / 营业收入",
             "收入转化为自由现金的能力", ">=5%", "0%~5%", "<0%"),
        _ind("经营现金流", _yi(ocf), "亿元", "经营活动现金流量净额",
             "主营业务的造血能力"),
    ]

    pattern = _cashflow_pattern(ocf, icf, ffcf)
    diagnosis = _cashflow_diagnosis(ocf, ni, free_cf, pattern)

    return {
        "available": ocf is not None,
        "indicators": indicators,
        "pattern": pattern,
        "chart": {
            "unit": "亿元",
            "operating": _yi(ocf), "investing": _yi(icf),
            "financing": _yi(ffcf), "net": _yi(net_cf),
        },
        "diagnosis": diagnosis,
        "summary": (f"经营现金流 {_yi(ocf):.1f} 亿元，自由现金流 "
                    f"{_yi(free_cf):.1f} 亿元。" if (ocf is not None and free_cf is not None)
                    else "现金流量表科目不足。"),
    }


def _cashflow_pattern(ocf, icf, ffcf):
    out = {"available": False, "name": "", "desc": "", "color": "pending", "signs": ""}
    if None in (ocf, icf, ffcf):
        return out
    o = 1 if ocf >= 0 else -1
    i = 1 if icf >= 0 else -1
    f = 1 if ffcf >= 0 else -1
    signs = f"{'＋' if o>0 else '－'} {'＋' if i>0 else '－'} {'＋' if f>0 else '－'}"
    # 典型成熟型 (+,-,-)
    if (o, i, f) == (1, -1, -1):
        name, desc, color = "成熟绩优型", "主业造血、投资扩张、对外还债分红，典型成熟优质企业现金流结构。", "normal"
    elif (o, i, f) == (1, -1, 1):
        name, desc, color = "成长扩张型", "主业造血且持续融资支撑投资扩张，典型成长期结构。", "normal"
    elif (o, i, f) == (1, 1, -1):
        name, desc, color = "收缩调整型", "主业造血并处置资产以偿债，可能在瘦身或战略调整。", "warning"
    elif (o, i, f) == (1, 1, 1):
        name, desc, color = "现金充沛型", "三项均流入，现金极充裕，需关注资金使用效率。", "normal"
    elif (o, i, f) == (1, -1, 0) or o == 1:
        name, desc, color = "稳健造血型", "主业经营现金为正，财务基础稳健。", "normal"
    elif (o, i, f) == (-1, -1, 1):
        name, desc, color = "初创扩张型", "主业尚未造血，靠融资支撑大举投资，高成长伴高风险。", "warning"
    elif (o, i, f) == (-1, 1, 1):
        name, desc, color = "衰退求生型", "主业失血，靠变卖资产与外部融资维持，警惕风险。", "danger"
    elif (o, i, f) == (-1, 1, -1):
        name, desc, color = "断臂求生型", "主业失血、变卖资产偿债，财务承压明显。", "danger"
    else:
        name, desc, color = "现金承压型", "经营现金为负，现金链偏紧，需关注融资接续。", "danger"
    out.update({"available": True, "name": name, "desc": desc, "color": color, "signs": signs})
    return out


def _cashflow_diagnosis(ocf, ni, free_cf, pattern):
    if ocf is None:
        return "现金流量表科目不足，无法完成现金流诊断。"
    parts = []
    if pattern.get("available"):
        parts.append(f"现金流结构（经营{pattern['signs']}）属「{pattern['name']}」：{pattern['desc']}")
    if ni is not None:
        ratio = ocf / ni if ni != 0 else None
        if ratio is not None:
            if ratio >= 1:
                parts.append(f"净现比 {ratio:.2f}，净利润有充足现金支撑，盈利质量高。")
            elif ratio >= 0.7:
                parts.append(f"净现比 {ratio:.2f}，利润现金含量尚可，关注应收/存货占用。")
            else:
                parts.append(f"净现比 {ratio:.2f} 偏低，存在'纸面利润'风险，利润未充分转化为现金。")
    if free_cf is not None:
        parts.append(f"自由现金流 {_yi(free_cf):.1f} 亿元，"
                     + ("覆盖资本开支后仍有盈余，分红/扩张有底气。" if free_cf > 0
                        else "资本开支超过经营造血，需外部融资补足。"))
    return "".join(parts)


# ── 4. Altman Z-score (破产预警) ─────────────────────────────────────────

def calculate_zscore(data: Dict[str, Any],
                     market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    md = market_data or {}
    ta = _v(data, "总资产", "资产总计")
    ca = _v(data, "流动资产", "流动资产合计")
    cl = _v(data, "流动负债", "流动负债合计")
    retained = _sum_opt(_v(data, "未分配利润"), _v(data, "盈余公积"))
    tl = _v(data, "总负债", "负债合计")
    rev = _v(data, "营业收入", "营业总收入")
    ebt = _v(data, "利润总额")
    fin = _v(data, "财务费用")
    eq = _v(data, *_EQUITY_KEYS)
    mcap = md.get("总市值")

    ebit = _sum_opt(ebt, fin) if ebt is not None else None
    working_capital = (ca - cl) if (ca is not None and cl is not None) else None
    x1 = _ratio(working_capital, ta)
    x2 = _ratio(retained, ta)
    x3 = _ratio(ebit, ta)
    x5 = _ratio(rev, ta)

    if mcap and tl:
        x4 = mcap / tl
        terms = [(1.2, x1), (1.4, x2), (3.3, x3), (0.6, x4), (1.0, x5)]
        model = "Z (上市制造业，X4 用股权市值)"
        safe, grey = 2.99, 1.81
    elif eq and tl:
        x4 = eq / tl
        terms = [(6.56, x1), (3.26, x2), (6.72, x3), (1.05, x4)]
        model = "Z'' (账面值口径，市值不可得)"
        safe, grey = 2.60, 1.10
    else:
        x4 = None
        terms = []
        model = "Z (数据不足)"
        safe, grey = 2.99, 1.81

    z = None
    if terms and all(t[1] is not None for t in terms):
        z = sum(coef * val for coef, val in terms)

    verdict, color = _z_verdict(z, safe, grey)

    _factor_meta = [
        ("X1 营运资本/总资产", x1, "(流动资产−流动负债)/总资产", "短期流动性"),
        ("X2 留存收益/总资产", x2, "(未分配利润+盈余公积)/总资产", "累积盈利"),
        ("X3 EBIT/总资产", x3, "息税前利润/总资产", "资产盈利能力"),
        ("X4 权益/负债", x4, "股权市值或账面净资产/总负债", "杠杆缓冲"),
        ("X5 营收/总资产", x5, "营业收入/总资产", "资产周转"),
    ]
    factor_inds = [_ind(n, _r(v), "", f, e) for n, v, f, e in _factor_meta]

    # 因子贡献分解：系数 × 因子值 = 对 Z 的贡献
    contributions = []
    if terms and all(t[1] is not None for t in terms):
        labels = [m[0] for m in _factor_meta]
        for (coef, val), label in zip(terms, labels):
            contributions.append({"name": label, "contribution": _r(coef * val),
                                   "coef": coef, "value": _r(val)})

    # 距临界值的差距
    distance = {"available": z is not None,
                "to_safe": _r(z - safe) if z is not None else None,
                "to_distress": _r(z - grey) if z is not None else None,
                "safe": safe, "grey": grey}

    diagnosis = _zscore_diagnosis(z, safe, grey, contributions, model)

    z_ind = {
        "name": "Z-Score", "value": _r(z),
        "display": f"{z:.2f}" if z is not None else "—", "unit": "",
        "formula": "Σ 系数 × 因子", "explanation": f"模型：{model}",
        "risk_level": "", "risk_color": color, "range": verdict,
    }
    return {
        "available": z is not None,
        "model": model,
        "z_score": _r(z),
        "verdict": verdict,
        "indicators": [z_ind] + factor_inds,
        "contributions": contributions,
        "distance": distance,
        "diagnosis": diagnosis,
        "chart": {
            "value": _r(z), "model": model, "safe": safe, "grey": grey,
            "verdict": verdict, "color": color,
            "zones": [
                {"label": "破产风险", "min": 0, "max": grey, "color": "danger"},
                {"label": "灰色地带", "min": grey, "max": safe, "color": "warning"},
                {"label": "安全区", "min": safe, "max": round(safe * 1.7, 2), "color": "normal"},
            ],
        },
        "summary": (f"Z 值 {z:.2f}，{verdict}。" if z is not None
                    else "Z-score 所需科目不足（总资产 / 负债等）。"),
    }


def _z_verdict(z, safe, grey):
    if z is None:
        return "数据不足", "pending"
    if z >= safe:
        return "财务稳健，破产风险低", "normal"
    if z >= grey:
        return "灰色地带，需持续观察", "warning"
    return "破产风险偏高，需警惕", "danger"


def _zscore_diagnosis(z, safe, grey, contributions, model):
    if z is None:
        return "总资产/负债等核心科目不足，无法完成 Z-score 诊断。"
    parts = [f"采用{model}，Z 值 {z:.2f}。"]
    if z >= safe:
        parts.append(f"高于安全线 {safe}，破产风险低，财务结构稳健。")
    elif z >= grey:
        parts.append(f"落在灰色地带（{grey}~{safe}），尚不能确认安全，需持续观察。")
    else:
        parts.append(f"低于警戒线 {grey}，破产风险显著偏高，需高度警惕。")
    if contributions:
        top = max(contributions, key=lambda c: c["contribution"] or 0)
        bottom = min(contributions, key=lambda c: c["contribution"] or 0)
        parts.append(f"贡献最大的是「{top['name']}」(+{top['contribution']:.2f})，"
                     f"最弱的是「{bottom['name']}」({bottom['contribution']:+.2f})，是主要拖累项。")
    return "".join(parts)


# ── 5. DCF (折现现金流估值) ───────────────────────────────────────────────

def calculate_dcf(data: Dict[str, Any], market_data: Optional[Dict[str, Any]] = None,
                  historical: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    md = market_data or {}
    ocf = _v(data, "经营活动现金流净额", "经营活动产生的现金流量净额")
    capex = _v(data, "购建固定资产支付的现金",
               "购建固定资产、无形资产和其他长期资产支付的现金")
    cash = _v(data, "货币资金")
    debt = _interest_bearing_debt(data)
    price = md.get("股价")
    shares = md.get("总股本")

    g1 = _infer_growth(historical)
    wacc, gp = DEFAULT_WACC, DEFAULT_PERPETUAL_GROWTH
    assumptions = {
        "WACC": f"{wacc*100:.0f}%",
        "首五年增长率": f"{g1*100:.1f}%（历史CAGR推导）",
        "永续增长率": f"{gp*100:.1f}%",
    }

    fcf0 = (ocf - capex) if (ocf is not None and capex is not None) else ocf
    if fcf0 is None or fcf0 <= 0:
        return {
            "available": False,
            "indicators": [_ind("自由现金流基数", _yi(fcf0), "亿元",
                                "经营现金流 − 资本支出", "DCF 估值基数")],
            "assumptions": assumptions,
            "note": "自由现金流为负或缺失，DCF 内在价值估值不适用（常见于高速扩张期）。",
            "chart": {},
            "summary": "DCF 不适用：自由现金流非正。",
        }

    net_debt = (debt or 0) - (cash or 0)

    # 分年度 FCF 预测明细
    pv_fcf, f = 0.0, fcf0
    projection = []
    for yr in range(1, 6):
        f = f * (1 + g1)
        disc = 1 / ((1 + wacc) ** yr)
        pv = f * disc
        pv_fcf += pv
        projection.append({"year": f"第{yr}年", "fcf": _yi(f),
                           "discount": _r(disc, 3), "pv": _yi(pv)})
    terminal_value = f * (1 + gp) / (wacc - gp)
    pv_terminal = terminal_value / ((1 + wacc) ** 5)
    enterprise_value = pv_fcf + pv_terminal
    tv_share = pv_terminal / enterprise_value * 100 if enterprise_value else None

    equity_value = enterprise_value - net_debt
    intrinsic_ps = equity_value / shares if shares else None
    margin = ((intrinsic_ps - price) / price * 100) if (intrinsic_ps and price) else None
    verdict, color = _dcf_verdict(margin)

    indicators = [
        _ind("自由现金流基数", _yi(fcf0), "亿元", "经营现金流 − 资本支出", "DCF 估值起点"),
        _ind("五年折现值", _yi(pv_fcf), "亿元", "Σ 各年FCF现值", "明确预测期价值"),
        _ind("终值现值", _yi(pv_terminal), "亿元", "永续终值折现", "永续期价值"),
        _ind("终值占比", tv_share, "%", "终值现值 / 企业价值",
             "估值对远期假设的依赖度（越高越敏感）", "<=70%", "70%~85%", ">85%"),
        _ind("企业价值EV", _yi(enterprise_value), "亿元", "五年折现值 + 终值现值", "经营资产现值"),
        _ind("净债务", _yi(net_debt), "亿元", "有息负债 − 货币资金", "需从EV扣除"),
        _ind("股权价值", _yi(equity_value), "亿元", "企业价值 − 净债务", "归属股东的内在价值"),
        _ind("每股内在价值", _r(intrinsic_ps), "元", "股权价值 / 总股本", "DCF 模型估算股价"),
        _ind("当前股价", _r(price), "元", "市场最新价", "市场定价"),
        _ind("安全边际", margin, "%", "(内在价值 − 股价) / 股价", "低估/高估幅度",
             ">=20%", "-20%~20%", "<-20%"),
    ]

    sensitivity = _dcf_sensitivity(fcf0, g1, wacc, gp, net_debt, shares)
    diagnosis = _dcf_diagnosis(intrinsic_ps, price, margin, tv_share, g1, wacc)

    return {
        "available": True,
        "indicators": indicators,
        "assumptions": assumptions,
        "projection": projection,
        "sensitivity": sensitivity,
        "verdict": verdict,
        "verdict_color": color,
        "diagnosis": diagnosis,
        "chart": {
            "intrinsic": _r(intrinsic_ps), "price": _r(price),
            "margin_pct": _r(margin), "ev_yi": _yi(enterprise_value),
        },
        "summary": (f"DCF 估算每股内在价值约 {intrinsic_ps:.2f} 元，{verdict}。"
                    if intrinsic_ps else
                    f"企业价值约 {_yi(enterprise_value):.0f} 亿元（缺股价/股本，无法折算每股）。"),
    }


def _dcf_equity_per_share(fcf0, g1, wacc, gp, net_debt, shares):
    pv, f = 0.0, fcf0
    for yr in range(1, 6):
        f *= (1 + g1)
        pv += f / ((1 + wacc) ** yr)
    tv = f * (1 + gp) / (wacc - gp)
    ev = pv + tv / ((1 + wacc) ** 5)
    equity = ev - net_debt
    return equity / shares if shares else None


def _dcf_sensitivity(fcf0, g1, wacc, gp, net_debt, shares):
    """3×3 per-share intrinsic value over WACC × perpetual-growth grid."""
    if not shares:
        return {"available": False}
    waccs = [wacc - 0.01, wacc, wacc + 0.01]
    gps = [gp - 0.005, gp, gp + 0.005]
    grid = []
    for w in waccs:
        row = []
        for g in gps:
            if w <= g:
                row.append(None)
            else:
                row.append(_r(_dcf_equity_per_share(fcf0, g1, w, g, net_debt, shares)))
        grid.append(row)
    return {
        "available": True,
        "wacc_labels": [f"{w*100:.0f}%" for w in waccs],
        "gp_labels": [f"{g*100:.1f}%" for g in gps],
        "grid": grid,
        "base_row": 1, "base_col": 1,
    }


def _dcf_diagnosis(intrinsic, price, margin, tv_share, g1, wacc):
    parts = []
    if intrinsic is not None and price is not None and margin is not None:
        if margin >= 20:
            parts.append(f"DCF 内在价值 {intrinsic:.2f} 元，较现价 {price:.2f} 元低估 {margin:.0f}%，存在安全边际。")
        elif margin <= -20:
            parts.append(f"DCF 内在价值 {intrinsic:.2f} 元，较现价 {price:.2f} 元高估 {abs(margin):.0f}%，需警惕回调。")
        else:
            parts.append(f"DCF 内在价值 {intrinsic:.2f} 元与现价 {price:.2f} 元接近，估值大致合理。")
    else:
        parts.append("缺股价/股本，仅给出企业价值，无法折算每股内在价值。")
    if tv_share is not None:
        if tv_share > 85:
            parts.append(f"终值占企业价值高达 {tv_share:.0f}%，估值高度依赖永续假设，对 WACC/永续增长极敏感，结论需谨慎。")
        else:
            parts.append(f"终值占比 {tv_share:.0f}%，明确预测期贡献相对扎实。")
    parts.append(f"关键假设：增长率 {g1*100:.1f}%、WACC {wacc*100:.0f}%，可参考下方敏感性矩阵看假设变动的影响。")
    return "".join(parts)


def _dcf_verdict(margin):
    if margin is None:
        return "无法对比市价", "info"
    if margin >= 20:
        return "相对市价明显低估", "normal"
    if margin <= -20:
        return "相对市价明显高估", "danger"
    return "估值大致合理", "warning"


# ── 6. Relative valuation (相对估值 + 夏普) ──────────────────────────────

def calculate_relative_valuation(data: Dict[str, Any],
                                 market_data: Optional[Dict[str, Any]] = None,
                                 historical: Optional[Dict[str, Any]] = None,
                                 peers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    md = market_data or {}
    rev = _v(data, "营业收入", "营业总收入")
    ni = _v(data, "净利润")
    eq = _v(data, *_EQUITY_KEYS)
    cash = _v(data, "货币资金")
    debt = _interest_bearing_debt(data)
    mcap = md.get("总市值")

    pe = md.get("市盈率")
    if pe is None and mcap and ni and ni > 0:
        pe = mcap / ni
    pb = md.get("市净率")
    if pb is None and mcap and eq and eq != 0:
        pb = mcap / eq
    ps = mcap / rev if (mcap and rev) else None
    growth_pct = _infer_growth(historical) * 100
    peg = (pe / growth_pct) if (pe and pe > 0 and growth_pct > 0) else None
    earnings_yield = (100 / pe) if (pe and pe > 0) else None
    div_yield = md.get("股息率")

    # EV 衍生倍数
    enterprise_value = (mcap + (debt or 0) - (cash or 0)) if mcap else None
    ev_sales = _ratio(enterprise_value, rev)
    ebit = _ebit(data)
    dep = _v(data, "固定资产折旧", "折旧费用", "固定资产折旧、油气资产折耗、生产性生物资产折旧")
    amort = _v(data, "无形资产摊销", "无形资产摊销费用")
    ebitda = _sum_opt(ebit, dep, amort) if ebit is not None else None
    ev_ebitda = _ratio(enterprise_value, ebitda)
    sharpe = _sharpe(md)

    indicators = [
        _ind("市盈率PE", pe, "倍", "总市值 / 净利润", "每元盈利的市场定价",
             "0~30", "30~50", ">50"),
        _ind("市净率PB", pb, "倍", "总市值 / 净资产", "每元净资产的市场定价",
             "0~3", "3~6", ">6"),
        _ind("市销率PS", ps, "倍", "总市值 / 营业收入", "每元收入的市场定价",
             "0~5", "5~10", ">10"),
        _ind("PEG", peg, "倍", "PE / 盈利增速", "估值与成长的匹配度",
             "0~1", "1~2", ">2"),
        _ind("盈利收益率", earnings_yield, "%", "1 / PE", "股权的盈利回报率（对标无风险利率）",
             ">=4%", "2.5%~4%", "<2.5%"),
        _ind("EV/Sales", ev_sales, "倍", "企业价值 / 营业收入", "含债口径的收入估值",
             "0~4", "4~8", ">8"),
        _ind("EV/EBITDA", ev_ebitda, "倍", "企业价值 / EBITDA", "剔除资本结构与折旧的估值",
             "0~12", "12~20", ">20"),
        _ind("股息率", div_yield, "%", "每股股利 / 股价", "现金分红回报"),
    ]
    if sharpe is not None:
        indicators.append(
            _ind("夏普比率", sharpe, "", "(年化收益 − 无风险利率) / 年化波动",
                 "风险调整后收益", ">=1", "0~1", "<0"))

    diagnosis = _relative_diagnosis(pe, pb, ps, peg, ev_ebitda, growth_pct)
    missing_notes = []
    if ev_ebitda is None and enterprise_value is not None:
        missing_notes.append("EV/EBITDA 缺折旧摊销科目未计算")
    if sharpe is None:
        missing_notes.append("夏普比率缺历史波动数据本期省略")

    return {
        "available": any(x is not None for x in (pe, pb, ps)),
        "indicators": indicators,
        "peers": peers or [],
        "chart": {
            "labels": ["PE", "PB", "PS", "PEG"],
            "company": [_r(pe), _r(pb), _r(ps), _r(peg)],
        },
        "diagnosis": diagnosis,
        "note": "增速取自历史盈利 CAGR。" + ("；".join(missing_notes) + "。" if missing_notes else ""),
        "summary": (f"PE {pe:.1f} 倍、PB {pb:.1f} 倍。"
                    if (pe is not None and pb is not None) else "市场估值数据不足。"),
    }


def _relative_diagnosis(pe, pb, ps, peg, ev_ebitda, growth_pct):
    if pe is None and pb is None and ps is None:
        return "缺市值/行情数据，相对估值无法判断。"
    parts = []
    if pe is not None:
        lvl = "偏低" if pe < 20 else "中性" if pe < 35 else "偏高"
        parts.append(f"PE {pe:.1f} 倍（估值{lvl}）。")
    if peg is not None:
        if peg < 1:
            parts.append(f"PEG {peg:.2f}＜1，成长性尚未被估值充分定价，性价比较好。")
        elif peg <= 2:
            parts.append(f"PEG {peg:.2f}，估值与成长基本匹配。")
        else:
            parts.append(f"PEG {peg:.2f}＞2，估值透支了成长预期，需警惕。")
    elif pe is not None:
        parts.append(f"当前盈利增速 {growth_pct:.1f}%，PEG 视成长兑现而定。")
    if pb is not None and pe is not None and pb > 5 and pe < 20:
        parts.append("PB 偏高而 PE 不高，反映高 ROE 溢价。")
    if ev_ebitda is not None:
        parts.append(f"EV/EBITDA {ev_ebitda:.1f} 倍，"
                     + ("剔除资本结构后估值合理。" if ev_ebitda <= 15 else "含债口径估值偏贵。"))
    return "".join(parts)


def _sharpe(md: Dict[str, Any]) -> Optional[float]:
    """Sharpe needs an annualized volatility series we don't reliably have.

    Returns None unless both annual return and a volatility proxy exist.
    """
    annual_return = md.get("近1年涨跌幅")
    vol = md.get("年化波动率")  # not provided by current snapshot adapters
    if annual_return is None or vol is None or vol == 0:
        return None
    return (annual_return / 100 - DEFAULT_RISK_FREE) / (vol / 100)


# ── 7. EVA (经济增加值) ──────────────────────────────────────────────────

def calculate_eva(data: Dict[str, Any],
                  market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    md = market_data or {}
    rev = _v(data, "营业收入", "营业总收入")
    op = _v(data, "营业利润")
    ebt = _v(data, "利润总额")
    tax = _v(data, "所得税费用")
    eq = _v(data, *_EQUITY_KEYS)
    debt = _interest_bearing_debt(data)

    tax_rate = DEFAULT_TAX_RATE
    if tax is not None and ebt is not None and ebt > 0:
        tr = tax / ebt
        if 0 < tr < 0.5:
            tax_rate = tr

    base_profit = op if op is not None else ebt
    nopat = base_profit * (1 - tax_rate) if base_profit is not None else None
    nopat_margin = _pct(nopat, rev)
    invested_capital = _sum_opt(debt, eq)
    roic = _pct(nopat, invested_capital)
    wacc_pct = DEFAULT_WACC * 100
    spread = (roic - wacc_pct) if roic is not None else None
    capital_charge = DEFAULT_WACC * invested_capital if invested_capital is not None else None
    eva = (nopat - capital_charge) if (nopat is not None and capital_charge is not None) else None
    eva_ratio = _pct(eva, invested_capital)
    cap_turnover = _ratio(rev, invested_capital)
    mcap = md.get("总市值")
    mva = (mcap - eq) if (mcap and eq is not None) else None

    indicators = [
        _ind("税后净营业利润NOPAT", _yi(nopat), "亿元", "营业利润×(1−税率)", "经营层面真实利润"),
        _ind("NOPAT利润率", nopat_margin, "%", "NOPAT/营业收入", "经营盈利质量", ">=5%", "2%~5%", "<2%"),
        _ind("投入资本", _yi(invested_capital), "亿元", "有息负债+股东权益", "占用的资本总额"),
        _ind("资本回报率ROIC", roic, "%", "NOPAT/投入资本", "投入资本的真实回报", ">=9%", "6%~9%", "<6%"),
        _ind("加权资本成本WACC", wacc_pct, "%", "假设值（行业近似）", "资本机会成本（价值门槛）"),
        _ind("ROIC−WACC利差", spread, "%", "ROIC−WACC", "价值创造的核心标尺", ">0", "", "<=0"),
        _ind("资本成本", _yi(capital_charge), "亿元", "WACC×投入资本", "资本的货币成本"),
        _ind("EVA经济增加值", _yi(eva), "亿元", "NOPAT−资本成本", "真正为股东创造的价值", ">0", "", "<=0"),
        _ind("EVA回报率", eva_ratio, "%", "EVA/投入资本", "单位资本的价值创造", ">=2%", "0%~2%", "<0%"),
        _ind("资本周转率", cap_turnover, "次", "营业收入/投入资本", "资本运营效率"),
    ]
    if mva is not None:
        indicators.append(_ind("市场增加值MVA", _yi(mva), "亿元", "总市值−账面净资产",
                               "市场对未来价值创造的预期"))

    verdict, color = _eva_value_verdict(spread)
    diagnosis = _eva_diagnosis(eva, roic, wacc_pct, spread, nopat_margin, cap_turnover)

    return {
        "available": eva is not None,
        "indicators": indicators,
        "roic_wacc": {
            "roic": _r(roic), "wacc": _r(wacc_pct), "spread": _r(spread),
            "verdict": verdict, "color": color,
        },
        "mva": {"available": mva is not None, "value": _yi(mva)},
        "assumptions": {"WACC": f"{DEFAULT_WACC*100:.0f}%", "所得税率": f"{tax_rate*100:.1f}%"},
        "chart": {
            "unit": "亿元", "nopat": _yi(nopat),
            "capital_charge": _yi(capital_charge), "eva": _yi(eva),
        },
        "diagnosis": diagnosis,
        "summary": (f"EVA {_yi(eva):.1f} 亿元，ROIC {roic:.1f}% vs WACC {wacc_pct:.0f}%，{verdict}。"
                    if (eva is not None and roic is not None) else "EVA 所需科目不足。"),
    }


def _eva_value_verdict(spread):
    if spread is None:
        return ("数据不足", "pending")
    if spread > 3:
        return ("显著创造价值", "normal")
    if spread > 0:
        return ("小幅创造价值", "normal")
    if spread > -3:
        return ("价值基本持平", "warning")
    return ("价值毁灭", "danger")


def _eva_diagnosis(eva, roic, wacc, spread, nopat_margin, cap_turnover):
    if eva is None or roic is None:
        return "核心科目缺失，无法完成 EVA 诊断。"
    if spread is not None and spread > 0:
        parts = [f"ROIC {roic:.1f}% 高于资本成本 WACC {wacc:.0f}%，利差 {spread:+.1f} 个百分点，"
                 f"企业在会计利润之外仍覆盖了资本机会成本，正为股东创造经济价值。"]
    else:
        parts = [f"ROIC {roic:.1f}% 未跑赢资本成本 WACC {wacc:.0f}%，利差 {spread:+.1f} 个百分点，"
                 f"会计上盈利但未能覆盖资本机会成本，存在价值毁灭风险。"]
    if nopat_margin is not None and cap_turnover is not None:
        if nopat_margin < 5:
            parts.append("改善方向：NOPAT 利润率偏低，应优先提升经营盈利（提价/降本/优化产品结构）。")
        elif cap_turnover < 0.6:
            parts.append("改善方向：资本周转偏慢，应盘活存量、压降低效投入资本（去库存/处置闲置产能）。")
        else:
            parts.append("改善方向：盈利与周转较均衡，可通过优化资本结构、适度杠杆降低 WACC 进一步提升 EVA。")
    return "".join(parts)


# ── 8. Harvard analytical framework (哈佛分析框架，LLM 定性) ─────────────

_HARVARD_DIMS = [
    ("strategy", "战略分析"),
    ("accounting", "会计分析"),
    ("financial", "财务分析"),
    ("prospective", "前景分析"),
]


def harvard_framework_analysis(state: Dict[str, Any],
                               llm_config: Optional[Dict[str, Any]] = None,
                               signals: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Qualitative four-dimension diagnosis with sub-points. LLM + rule fallback."""
    signals = signals or {}
    if llm_config and llm_config.get("api_key"):
        try:
            from nodes.llm_client import call_llm
            prompt = (
                "你是资深财务分析专家，运用哈佛分析框架（战略分析、会计分析、财务分析、前景分析）"
                "对公司做综合诊断。\n\n## 要求\n返回 JSON：\n{\n"
                '  "strategy": {"score": 0-100整数, "text": "战略与商业模式诊断(40-70字)", "points": ["要点1","要点2"]},\n'
                '  "accounting": {"score": 0-100整数, "text": "会计质量与盈余可靠性(40-70字)", "points": ["要点1","要点2"]},\n'
                '  "financial": {"score": 0-100整数, "text": "盈利/偿债/营运能力(40-70字)", "points": ["要点1","要点2"]},\n'
                '  "prospective": {"score": 0-100整数, "text": "未来前景与风险(40-70字)", "points": ["要点1","要点2"]},\n'
                '  "conclusion": "一句话综合结论(50字内)"\n}\n\n'
                "## 约束\n1. 只基于给定数据，不编造\n2. score 越高越健康\n3. 每个维度 points 给 2-3 条短要点\n"
                "4. 只输出 JSON，不加 markdown"
            )
            resp = call_llm(prompt, _harvard_context(state, signals), config={"timeout": 25})
            data = _parse_json_obj(resp)
            if data:
                dims = []
                for k, name in _HARVARD_DIMS:
                    d = data.get(k, {}) or {}
                    pts = d.get("points") or []
                    dims.append({
                        "key": k, "name": name,
                        "score": _coerce_int(d.get("score")),
                        "text": str(d.get("text", "")).strip(),
                        "points": [str(p).strip() for p in pts if str(p).strip()][:3],
                    })
                if any(d["text"] for d in dims):
                    return {"available": True, "source": "llm",
                            "dimensions": dims,
                            "conclusion": str(data.get("conclusion", "")).strip()}
        except Exception:
            pass
    return _harvard_fallback(state, signals)


def _harvard_context(state: Dict[str, Any], signals: Dict[str, Any]) -> str:
    ws = state.get("weighted_score", {}) or {}
    anomalies = state.get("anomaly_signals", []) or []
    linkage = state.get("linkage_diagnosis", []) or []
    parts = [
        f"公司：{state.get('company_name', '')}",
        f"细分赛道：{state.get('sector_level2', '')}",
        f"综合评分：{ws.get('score', 'NA')}",
        f"异常信号数：{len(anomalies)}",
        f"联动诊断数：{len(linkage)}",
    ]
    # computed model signals for grounding
    smap = [("ROE", signals.get("roe"), "%"), ("ROIC", signals.get("roic"), "%"),
            ("ROIC-WACC利差", signals.get("spread"), "pp"), ("Z-Score", signals.get("zscore"), ""),
            ("DCF安全边际", signals.get("margin"), "%")]
    sig_txt = "；".join(f"{n}{v}{u}" for n, v, u in smap if v is not None)
    if sig_txt:
        parts.append("关键测算：" + sig_txt)
    if signals.get("zscore_verdict"):
        parts.append(f"破产预警：{signals['zscore_verdict']}")
    if signals.get("cash_pattern"):
        parts.append(f"现金流结构：{signals['cash_pattern']}")
    if anomalies:
        parts.append("主要异常：" + "；".join(
            str(a.get("description", a.get("title", "")))[:40] for a in anomalies[:3]))
    return "\n".join(parts)


def _harvard_fallback(state: Dict[str, Any], signals: Dict[str, Any]) -> Dict[str, Any]:
    ws = state.get("weighted_score", {}) or {}
    score = ws.get("score")
    anomalies = state.get("anomaly_signals", []) or []
    base = int(score) if isinstance(score, (int, float)) else 60
    penalty = min(len(anomalies) * 5, 25)
    sector = state.get("sector_level2", "锂电")
    roe, roic, spread = signals.get("roe"), signals.get("roic"), signals.get("spread")
    zv, cash = signals.get("zscore_verdict"), signals.get("cash_pattern")
    margin, driver = signals.get("margin"), signals.get("driver")

    # financial score blends weighted score with value-creation signal
    fin_score = max(0, base - penalty)
    if spread is not None:
        fin_score = max(0, min(100, int(fin_score + (5 if spread > 0 else -8))))

    def pts(*xs):
        return [x for x in xs if x]

    dims = [
        {"key": "strategy", "name": "战略分析",
         "score": base,
         "text": f"处于{sector}赛道，{('盈利驱动为' + driver) if driver else '需结合行业景气评估竞争定位'}。",
         "points": pts(f"赛道：{sector}",
                       f"ROE 驱动类型：{driver}" if driver else None,
                       "竞争定位需结合产能与成本曲线")},
        {"key": "accounting", "name": "会计分析",
         "score": max(0, 80 - penalty),
         "text": ("存在需关注的会计/异常信号，" if anomalies else "未见显著会计异常，") + f"异常 {len(anomalies)} 项。",
         "points": pts(f"异常信号 {len(anomalies)} 项",
                       f"现金流结构：{cash}" if cash else None,
                       "盈余质量见现金流板块净现比")},
        {"key": "financial", "name": "财务分析",
         "score": fin_score,
         "text": (f"ROE {roe:.1f}%、ROIC {roic:.1f}%" if (roe is not None and roic is not None)
                  else f"综合评分 {base} 分") + "，盈利与偿债详见各板块。",
         "points": pts(f"ROE {roe:.1f}%" if roe is not None else None,
                       f"ROIC−WACC 利差 {spread:+.1f}pp" if spread is not None else None,
                       f"破产预警：{zv}" if zv else None)},
        {"key": "prospective", "name": "前景分析",
         "score": base,
         "text": "前景取决于赛道供需、产能释放节奏与估值安全边际。",
         "points": pts(f"DCF 安全边际 {margin:+.0f}%" if margin is not None else None,
                       f"价值创造：{('正' if (spread or 0) > 0 else '承压')}",
                       "关注锂价与产能利用率")},
    ]
    conclusion = "综合健康度{}（规则化兜底，未启用 LLM 定性分析）。".format(
        "良好" if base >= 70 else "中等" if base >= 50 else "偏弱")
    return {"available": True, "source": "rule", "dimensions": dims, "conclusion": conclusion}


def _coerce_int(v) -> Optional[int]:
    try:
        return int(round(float(v)))
    except (ValueError, TypeError):
        return None


def _parse_json_obj(resp: Optional[str]) -> Optional[Dict[str, Any]]:
    if not resp or len(resp.strip()) < 5:
        return None
    import json
    cleaned = resp.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


# ── Orchestrator ─────────────────────────────────────────────────────────

def run_advanced_analysis(state: Dict[str, Any],
                          llm_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute all four advanced sections from a pipeline state dict."""
    data: Dict[str, Any] = {}
    data.update(state.get("financial_data") or {})
    data.update(state.get("notes_data") or {})
    market = state.get("market_data") or {}
    historical = state.get("historical_data") or {}
    peers = state.get("_peer_comparison") or []

    # Numeric models first, so Harvard (qualitative) can be grounded in them.
    dupont = calculate_dupont(data, historical)
    cvp = calculate_cvp(data)
    cashflow = calculate_cashflow_health(data)
    zscore = calculate_zscore(data, market)
    dcf = calculate_dcf(data, market, historical)
    relative = calculate_relative_valuation(data, market, historical, peers)
    eva = calculate_eva(data, market)

    signals = {
        "roe": (dupont.get("chart") or {}).get("roe"),
        "driver": (dupont.get("driver_type") or [None])[0],
        "zscore": zscore.get("z_score"),
        "zscore_verdict": zscore.get("verdict"),
        "cash_pattern": (cashflow.get("pattern") or {}).get("name"),
        "roic": (eva.get("roic_wacc") or {}).get("roic"),
        "spread": (eva.get("roic_wacc") or {}).get("spread"),
        "margin": (dcf.get("chart") or {}).get("margin_pct"),
    }
    harvard = harvard_framework_analysis(state, llm_config, signals)

    return {
        "business": {"dupont": dupont, "cvp": cvp},
        "risk": {"cashflow": cashflow, "zscore": zscore},
        "valuation": {"dcf": dcf, "relative": relative},
        "strategy": {"harvard": harvard, "eva": eva},
        "market_data": market,
    }

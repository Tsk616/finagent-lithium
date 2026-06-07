"""
Node 2: validate_data — 数据校验

Performs:
  1. Collects all accounts needed for the matched sector's indicators
     (from KB or formula extraction).
  2. Resolves account names in financial_data via alias matching.
  3. Flags missing accounts and unreadable indicators in error_log.
  4. Basic numeric sanity checks (revenue > 0, total_assets > 0, etc.).
"""

import re
from typing import Dict, List, Optional, Set, Any

# ── Standalone account alias mapping ──────────────────────────────────
# Built-in fallback when KB is unavailable. Merged from:
#   - 04_data_validation.md spec table (~100+ standard accounts)
#   - lithium_knowledge_base.json account_mapping
#
# Format: canonical_name → [aliases...]
# The loader normalizes all aliases (lowercase, strip) for matching.

_BUILTIN_ACCOUNT_MAPPING: Dict[str, List[str]] = {
    # ── 利润表 ──
    "营业收入": ["营业收入", "营业总收入", "Revenue", "Total Revenue", "收入", "销售收入"],
    "营业成本": ["营业成本", "营业总成本", "Cost of Revenue", "Cost of Sales", "销售成本"],
    "毛利": ["毛利", "Gross Profit"],
    "销售费用": ["销售费用", "Selling Expenses", "Selling and Marketing Expenses"],
    "管理费用": ["管理费用", "Administrative Expenses", "General and Administrative Expenses",
               "管理费用及研发费用"],
    "研发费用": ["研发费用", "Research and Development Expenses", "R&D Expenses", "研发支出"],
    "财务费用": ["财务费用", "Finance Costs", "Financial Expenses", "利息费用"],
    "扣非净利润": ["扣除非经常性损益的净利润", "扣非净利润",
                 "Net Profit Excluding Non-recurring Items", "经常性净利润"],
    "净利润": ["净利润", "归母净利润", "归属于上市公司股东的净利润", "Net Profit", "Net Income"],
    "营业外收入": ["营业外收入", "Non-operating Income"],
    "营业外支出": ["营业外支出", "Non-operating Expenses"],
    "资产减值损失": ["资产减值损失", "信用减值损失", "Asset Impairment Loss", "Credit Impairment Loss"],
    "投资收益": ["投资收益", "Investment Income"],
    "所得税费用": ["所得税费用", "Income Tax Expense"],
    "利息费用": ["利息费用", "财务费用-利息支出", "Interest Expense"],
    "营业利润": ["营业利润", "Operating Profit"],
    "利润总额": ["利润总额", "Total Profit", "Profit Before Tax"],
    "所得税": ["所得税", "Income Tax"],
    "基本每股收益": ["基本每股收益", "EPS", "Basic Earnings Per Share"],
    "稀释每股收益": ["稀释每股收益", "Diluted EPS"],

    # ── 资产负债表 ──
    "流动资产": ["流动资产", "Current Assets", "流动资产合计"],
    "流动负债": ["流动负债", "Current Liabilities", "流动负债合计"],
    "存货": ["存货", "Inventories", "Inventory"],
    "预付款项": ["预付款项", "预付账款", "Prepayments", "Prepaid Expenses"],
    "应收账款": ["应收账款", "应收票据及应收账款", "应收款项融资",
               "Notes and Accounts Receivable", "Trade Receivables"],
    "固定资产": ["固定资产", "Fixed Assets", "Property, Plant and Equipment",
               "PP&E", "固定资产净值"],
    "总资产": ["总资产", "资产总计", "Total Assets"],
    "总负债": ["总负债", "负债总计", "Total Liabilities"],
    "净资产": ["净资产", "所有者权益", "股东权益", "Equity", "Shareholders' Equity"],
    "在建工程": ["在建工程", "Construction in Progress", "CIP"],
    "合同负债": ["合同负债", "预收款项", "Contract Liabilities", "Advances from Customers", "预收账款"],
    "货币资金": ["货币资金", "Cash and Cash Equivalents", "现金及现金等价物"],
    "短期借款": ["短期借款", "Short-term Borrowings"],
    "长期借款": ["长期借款", "Long-term Borrowings"],
    "应付账款": ["应付账款", "Accounts Payable", "Trade Payables"],
    "预收款项": ["预收款项", "预收账款", "Advances from Customers"],
    "其他应收款": ["其他应收款", "Other Receivables"],
    "其他应付款": ["其他应付款", "Other Payables"],
    "商誉": ["商誉", "Goodwill"],
    "无形资产": ["无形资产", "Intangible Assets"],
    "开发支出": ["开发支出", "Development Expenditure"],
    "长期待摊费用": ["长期待摊费用", "Long-term Deferred Expenses"],
    "应付职工薪酬": ["应付职工薪酬", "Salaries and Welfare Payable"],
    "应交税费": ["应交税费", "Taxes Payable"],
    "一年内到期的非流动负债": ["一年内到期的非流动负债", "Current Portion of Non-current Liabilities"],
    "长期应付款": ["长期应付款", "Long-term Payables"],
    "专项储备": ["专项储备", "Special Reserve"],
    "盈余公积": ["盈余公积", "Surplus Reserve"],
    "未分配利润": ["未分配利润", "Retained Earnings"],
    "少数股东权益": ["少数股东权益", "Minority Interests"],
    "实收资本": ["实收资本", "股本", "Paid-in Capital", "Share Capital"],
    "资本公积": ["资本公积", "Capital Reserve"],
    "其他综合收益": ["其他综合收益", "Other Comprehensive Income"],

    # ── 现金流量表 ──
    "经营活动现金流净额": ["经营活动产生的现金流量净额", "经营活动现金流净额",
                       "Net Cash from Operating Activities", "Operating Cash Flow"],
    "销售商品收到的现金": ["销售商品、提供劳务收到的现金", "Cash from Sales",
                        "Cash Received from Sales"],
    "购买商品支付的现金": ["购买商品、接受劳务支付的现金", "Cash Paid for Purchases",
                        "Cash Paid for Goods"],
    "支付给职工的现金": ["支付给职工以及为职工支付的现金", "Cash Paid to Employees"],
    "支付的各项税费": ["支付的各项税费", "Taxes Paid"],
    "支付其他经营活动现金": ["支付其他与经营活动有关的现金",
                          "Other Cash Paid Relating to Operating Activities"],
    "收回投资收到的现金": ["收回投资收到的现金", "Cash from Disposal of Investments"],
    "取得投资收益收到的现金": ["取得投资收益收到的现金", "Cash from Investment Income"],
    "处置固定资产收回的现金": ["处置固定资产、无形资产和其他长期资产收回的现金",
                           "Cash from Disposal of Assets"],
    "购建固定资产支付的现金": ["购建固定资产、无形资产和其他长期资产支付的现金",
                           "Cash Paid for Acquisition of Assets"],
    "投资支付的现金": ["投资支付的现金", "Cash Paid for Investments"],
    "吸收投资收到的现金": ["吸收投资收到的现金", "Cash from Capital Contributions"],
    "取得借款收到的现金": ["取得借款收到的现金", "Cash from Borrowings"],
    "偿还债务支付的现金": ["偿还债务支付的现金", "Cash Repayment of Borrowings"],
    "分配股利利润支付的现金": ["分配股利、利润或偿付利息支付的现金",
                           "Cash Paid for Dividends and Interest"],

    # ── 期初期末 ──
    "期初存货": ["期初存货", "Beginning Inventory"],
    "期末存货": ["期末存货", "Ending Inventory"],
    "期初应收": ["期初应收账款", "Beginning Accounts Receivable"],
    "期末应收": ["期末应收账款", "Ending Accounts Receivable"],
    "期初固定资产": ["期初固定资产", "Beginning Fixed Assets"],
    "期末固定资产": ["期末固定资产", "Ending Fixed Assets"],
    "期初在建工程": ["期初在建工程", "Beginning Construction in Progress"],
    "期末在建工程": ["期末在建工程", "Ending Construction in Progress"],
    "期初合同负债": ["期初合同负债", "Beginning Contract Liabilities"],
    "期末合同负债": ["期末合同负债", "Ending Contract Liabilities"],
    "期初净资产": ["期初净资产", "Beginning Equity"],
    "期末净资产": ["期末净资产", "Ending Equity"],
    "期初总资产": ["期初总资产", "Beginning Total Assets"],
    "期末总资产": ["期末总资产", "Ending Total Assets"],
    "期初总负债": ["期初总负债", "Beginning Total Liabilities"],
    "期末总负债": ["期末总负债", "Ending Total Liabilities"],
    "期初应收账款": ["期初应收账款", "期初应收票据及应收账款",
                     "Beginning Notes and Accounts Receivable"],
    "期末应收账款": ["期末应收账款", "期末应收票据及应收账款",
                     "Ending Notes and Accounts Receivable"],
    "期初流动资产": ["期初流动资产", "Beginning Current Assets"],
    "期末流动资产": ["期末流动资产", "Ending Current Assets"],
    "期初流动负债": ["期初流动负债", "Beginning Current Liabilities"],
    "期末流动负债": ["期末流动负债", "Ending Current Liabilities"],

    # ── 附注相关 ──
    "存货跌价损失": ["存货跌价损失", "存货跌价准备", "Inventory Impairment Loss",
                     "Provision for Inventory Decline"],
    "信用减值损失": ["信用减值损失", "坏账损失", "Credit Impairment Loss",
                     "Provision for Bad Debts"],
    "资产处置收益": ["资产处置收益", "资产处置损益", "Gain on Disposal of Assets"],
    "政府补助": ["政府补助", "其他收益", "Government Grants", "Other Income"],
    "汇兑损益": ["汇兑损益", "汇兑损失", "Exchange Gain/Loss", "Foreign Exchange Difference"],
    "加权平均净资产": ["加权平均净资产", "Average Equity"],
    "平均固定资产": ["平均固定资产", "Average Fixed Assets"],
    "平均存货": ["平均存货", "Average Inventory"],
    "平均应收账款": ["平均应收账款", "Average Accounts Receivable"],
    "平均总资产": ["平均总资产", "Average Total Assets"],
    "平均净资产": ["平均净资产", "Average Equity"],
    "营业收入上期": ["上期营业收入", "上年营业收入", "Previous Period Revenue"],
    "应收账款上期": ["上期应收账款", "上年应收账款", "Previous Period Receivables"],
    "存货上期": ["上期存货", "上年存货", "Previous Period Inventory"],
}


# ── Numeric sanity checks ─────────────────────────────────────────────

def _numeric_checks(financial_data: Dict[str, Optional[float]],
                    resolved: Dict[str, str]) -> List[str]:
    """Run basic numeric sanity checks on financial data.

    Returns a list of warning/error messages for error_log.
    """
    issues: List[str] = []

    # Map canonical names to their resolved keys in financial_data
    reverse_map: Dict[str, str] = {}
    for original_key, canonical in resolved.items():
        reverse_map[canonical] = original_key

    def get_val(canonical_name: str) -> Optional[float]:
        key = reverse_map.get(canonical_name)
        if key:
            return financial_data.get(key)
        return None

    revenue = get_val("营业收入")
    if revenue is not None and revenue <= 0:
        issues.append(f"[数值异常] 营业收入={revenue}，应为正数。")

    total_assets = get_val("总资产")
    if total_assets is not None and total_assets <= 0:
        issues.append(f"[数值异常] 总资产={total_assets}，应为正数。")

    current_assets = get_val("流动资产")
    current_liabilities = get_val("流动负债")
    if (current_assets is not None and current_liabilities is not None
            and current_assets < current_liabilities):
        issues.append(
            f"[流动风险] 流动资产({current_assets}) < 流动负债({current_liabilities})，"
            f"存在短期偿债压力。")

    total_liabilities = get_val("总负债")
    equity = get_val("净资产")
    if (total_assets is not None and total_liabilities is not None
            and equity is not None):
        expected = total_liabilities + equity
        if abs(total_assets - expected) > max(total_assets * 0.01, 1.0):
            issues.append(
                f"[恒等式失衡] 总资产({total_assets}) ≠ 总负债({total_liabilities}) "
                f"+ 净资产({equity}) = {expected}，差值={total_assets - expected:.1f}。")

    return issues


# ── Account resolver ──────────────────────────────────────────────────

class AccountResolver:
    """Resolves raw account names to canonical names using built-in + KB mappings."""

    def __init__(self, kb: Any = None):
        """Build the alias→canonical lookup table.

        Args:
            kb: Optional LithiumKnowledgeBase instance. Its account_mapping
                is merged with the built-in mapping.
        """
        # canonical_name → [normalized_aliases...]
        self._canonical_aliases: Dict[str, List[str]] = {}

        # Load built-in mappings
        for canonical, aliases in _BUILTIN_ACCOUNT_MAPPING.items():
            self._canonical_aliases[canonical] = list(aliases)

        # Merge KB mappings (KB takes precedence for canonical names)
        if kb and hasattr(kb, "get_raw"):
            kb_data = kb.get_raw()
            kb_mapping = kb_data.get("account_mapping", {})
            for canonical, aliases in kb_mapping.items():
                if canonical in self._canonical_aliases:
                    # Merge: add KB aliases not in built-in
                    existing = set(self._canonical_aliases[canonical])
                    for a in aliases:
                        if a not in existing:
                            self._canonical_aliases[canonical].append(a)
                else:
                    self._canonical_aliases[canonical] = list(aliases)

        # Build alias→canonical lookup
        self._alias_to_canonical: Dict[str, str] = {}

        # Phase 1: Map each canonical name to itself (self-mapping takes priority)
        for canonical in self._canonical_aliases:
            ck = self._normalize(canonical)
            if ck:
                self._alias_to_canonical[ck] = canonical

        # Phase 2: Map aliases, but never overwrite an existing entry
        # (so self-mappings and first-come aliases win over conflicts)
        for canonical, aliases in self._canonical_aliases.items():
            for alias in aliases:
                key = self._normalize(alias)
                if key and key not in self._alias_to_canonical:
                    self._alias_to_canonical[key] = canonical

    @staticmethod
    def _normalize(s: str) -> str:
        """Normalize: lowercase, strip whitespace, collapse internal spaces."""
        if not s:
            return ""
        return re.sub(r"\s+", " ", s.strip().lower())

    def resolve(self, account_name: str) -> Optional[str]:
        """Resolve a raw account name to its canonical form.

        Returns None if the account is unrecognized.
        """
        key = self._normalize(account_name)
        if not key:
            return None

        # Exact match
        if key in self._alias_to_canonical:
            return self._alias_to_canonical[key]

        # Try stripping common suffixes
        suffixes = ["本期", "期末", "期初", "余额", "净额", "合计", "总额", "原值", "净值"]
        for suffix in suffixes:
            suffix_norm = self._normalize(suffix)
            if key.endswith(suffix_norm):
                stripped = key[:-len(suffix_norm)].strip()
                if stripped in self._alias_to_canonical:
                    return self._alias_to_canonical[stripped]

        return None

    def is_recognized(self, account_name: str) -> bool:
        """Check if an account name can be resolved."""
        return self.resolve(account_name) is not None

    def get_canonical_name(self, account_name: str) -> str:
        """Resolve or return the original name with '未识别-' prefix."""
        resolved = self.resolve(account_name)
        if resolved:
            return resolved
        return f"未识别科目: {account_name}"


# ── Main node function ────────────────────────────────────────────────

def validate_data(
    financial_data: Optional[Dict[str, Optional[float]]] = None,
    notes_data: Optional[Dict[str, Optional[float]]] = None,
    sector_level1: Optional[str] = None,
    sector_level2: Optional[str] = None,
    sub_sectors: Optional[List[str]] = None,
    kb: Any = None,
) -> Dict[str, Any]:
    """Validate financial data completeness and sanity.

    Args:
        financial_data: {account_name: value} — main financial accounts.
        notes_data: {account_name: value} — notes/additional data.
        sector_level1: Identified level-1 sector (for logging).
        sector_level2: Identified level-2 sector (for logging).
        sub_sectors: Sub-sectors for integrated companies (for accounts collection).
        kb: LithiumKnowledgeBase instance.

    Returns:
        Dict with:
            error_log: List[str] of issues found.
            validated: bool — True if no blocking errors.
            resolved_accounts: Dict[str, Optional[str]] — raw→canonical mapping.
            missing_accounts: List[str] — canonical names of required but missing accounts.
            unrecognized_accounts: List[str] — raw names not in any mapping.
            data_completeness: float — 0.0 to 1.0 fraction of required accounts present.
    """
    financial_data = financial_data or {}
    notes_data = notes_data or {}
    error_log: List[str] = []
    resolved_accounts: Dict[str, Optional[str]] = {}

    # Build resolver
    resolver = AccountResolver(kb)

    # ── Step 1: Collect required accounts ──
    required_accounts = _collect_required_accounts(sector_level2, sub_sectors, kb)

    # ── Step 2: Resolve incoming account names ──
    all_input_data = {**financial_data, **notes_data}

    for raw_name in all_input_data:
        canonical = resolver.resolve(raw_name)
        resolved_accounts[raw_name] = canonical
        if canonical is None:
            error_log.append(f"[未识别科目] '{raw_name}' — 无法匹配到标准科目，请检查科目名或补充别名。")

    # ── Step 3: Check which required accounts are missing ──
    present_canonicals = set()
    for canonical in resolved_accounts.values():
        if canonical and not canonical.startswith("未识别科目"):
            present_canonicals.add(canonical)

    missing_accounts: List[str] = []
    for required in required_accounts:
        if required not in present_canonicals:
            missing_accounts.append(required)

    if missing_accounts:
        missing_list = "、".join(missing_accounts[:20])
        if len(missing_accounts) > 20:
            missing_list += f"... 等共{len(missing_accounts)}项"
        error_log.append(
            f"[数据缺失] 赛道「{sector_level2 or sector_level1 or '未知'}」"
            f"需要以下科目但未提供: {missing_list}。"
            f"相关指标将标记为 None。")

    # ── Step 4: Numeric sanity checks ──
    numeric_issues = _numeric_checks(financial_data, resolved_accounts)
    error_log.extend(numeric_issues)

    # ── Step 5: Compute completeness ──
    if required_accounts:
        completeness = len(set(required_accounts) & present_canonicals) / len(required_accounts)
    else:
        completeness = 1.0

    # ── Step 6: Unrecognized accounts ──
    unrecognized = [
        raw for raw, canonical in resolved_accounts.items()
        if canonical is None or canonical.startswith("未识别科目")
    ]

    return {
        "error_log": error_log,
        "validated": len([e for e in error_log if e.startswith("[数值异常]")]) == 0,
        "resolved_accounts": resolved_accounts,
        "missing_accounts": missing_accounts,
        "unrecognized_accounts": unrecognized,
        "data_completeness": completeness,
    }


def _collect_required_accounts(
    sector_level2: Optional[str],
    sub_sectors: Optional[List[str]],
    kb: Any,
) -> Set[str]:
    """Collect all account names needed for the sector's indicators.

    Priority:
      1. KB indicator accounts_needed field (when populated)
      2. Formula extraction from KB indicator formulas

    For integrated companies (sub_sectors), collects for all sub-sectors.
    """
    accounts: Set[str] = set()

    # Always include general indicators' accounts
    if kb and hasattr(kb, "get_general_indicators"):
        for ind in kb.get_general_indicators():
            for acct in ind.get("accounts_needed", []):
                if acct:
                    accounts.add(acct)
            # Also extract from formula
            for acct in _extract_accounts_from_formula(ind.get("formula", "")):
                accounts.add(acct)

    # Determine which sector codes to query
    codes_to_check: List[str] = []
    if sub_sectors:
        codes_to_check.extend(sub_sectors)
    elif sector_level2:
        # Try to find the sector code from the level2 name
        # (The caller should ideally pass the sector code; we try to work with what we have)
        pass

    # If we have a KB and can get sector indicators, collect their accounts
    if kb and hasattr(kb, "get_sector"):
        # If we have sub_sectors, use those codes
        for code in codes_to_check:
            sector = kb.get_sector(code)
            if sector:
                for cat_inds in sector.get("indicators", {}).values():
                    for ind in cat_inds:
                        for acct in ind.get("accounts_needed", []):
                            if acct:
                                accounts.add(acct)
                        for acct in _extract_accounts_from_formula(ind.get("formula", "")):
                            accounts.add(acct)

    # Fallback: mandatory accounts every financial analysis needs
    if not accounts:
        accounts = {
            "营业收入", "营业成本", "净利润", "总资产", "总负债",
            "流动资产", "流动负债", "存货", "应收账款", "固定资产",
            "货币资金", "经营活动现金流净额",
        }

    return accounts


def _extract_accounts_from_formula(formula: str) -> List[str]:
    """Extract potential Chinese account names from a formula string.

    Heuristic: split on operators/numbers and keep segments with Chinese characters.
    """
    if not formula:
        return []
    # Split on operators, numbers, punctuation, whitespace
    # Split on operators: include both regular hyphen U+002D and minus sign U+2212
    parts = re.split(r'[÷+\-−×*/()（）\d.%=\s\n;；，,、]+', formula)
    tokens = []
    for p in parts:
        p = p.strip()
        # Must contain Chinese (likely an account name) and be at least 4 chars
        # (Shorter segments like "销售", "管理", "财务" are false positives from
        # abbreviated formulas like (销售+管理+财务+研发费用); skip them.)
        if p and re.search(r'[一-鿿]', p) and len(p) >= 4:
            # Filter out formula fragments that aren't account names
            if p not in ("乘以", "除以", "减去", "加上", "其中", "合计", "平均", "当期",
                         "上期", "本期", "期末", "期初", "余额", "平均值", "科目余额"):
                tokens.append(p)
    return tokens


# ── Test helpers ──────────────────────────────────────────────────────

def _test_data_sample(company: str) -> Dict[str, Optional[float]]:
    """Return sample financial data for a well-known company (for testing)."""
    samples = {
        "宁德时代": {
            "营业收入": 400000000000.0,
            "营业成本": 300000000000.0,
            "净利润": 44000000000.0,
            "总资产": 670000000000.0,
            "总负债": 450000000000.0,
            "净资产": 220000000000.0,
            "流动资产": 420000000000.0,
            "流动负债": 300000000000.0,
            "存货": 45000000000.0,
            "应收账款": 65000000000.0,
            "固定资产": 180000000000.0,
            "货币资金": 280000000000.0,
            "经营活动现金流净额": 92000000000.0,
            "合同负债": 22000000000.0,
            "扣非净利润": 40000000000.0,
            "研发费用": 18000000000.0,
            "销售费用": 12000000000.0,
            "管理费用": 8000000000.0,
            "财务费用": -2000000000.0,
        },
        "天齐锂业": {
            "营业收入": 40000000000.0,
            "营业成本": 15000000000.0,
            "净利润": 7000000000.0,
            "总资产": 70000000000.0,
            "总负债": 25000000000.0,
            "净资产": 45000000000.0,
            "流动资产": 30000000000.0,
            "流动负债": 12000000000.0,
            "存货": 5000000000.0,
            "应收账款": 3000000000.0,
            "固定资产": 25000000000.0,
            "货币资金": 8000000000.0,
            "经营活动现金流净额": 12000000000.0,
            "合同负债": 3000000000.0,
            "扣非净利润": 6500000000.0,
        },
        "比亚迪": {
            "营业收入": 600000000000.0,
            "营业成本": 480000000000.0,
            "净利润": 30000000000.0,
            "总资产": 680000000000.0,
            "总负债": 520000000000.0,
            "净资产": 160000000000.0,
            "流动资产": 280000000000.0,
            "流动负债": 350000000000.0,
            "存货": 80000000000.0,
            "应收账款": 50000000000.0,
            "固定资产": 250000000000.0,
            "货币资金": 90000000000.0,
            "经营活动现金流净额": 160000000000.0,
            "合同负债": 35000000000.0,
            "扣非净利润": 25000000000.0,
            "研发费用": 40000000000.0,
            "销售费用": 25000000000.0,
            "管理费用": 18000000000.0,
        },
    }
    return samples.get(company, {})

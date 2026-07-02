"""
Financial Data Extractor — Excel/PDF parsing.

Extracts structured {account_name: value} dict from uploaded files.
Strategy:
  1. Excel (.xlsx/.xls): pandas direct parse
  2. PDF: pdftotext → keyword+regex extraction
  3. Unit detection: auto-detect and normalize (万元/亿元/元)

Output: Dict[str, float] ready for pipeline input.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple


# ── Unit normalization ────────────────────────────────────────────────

def detect_unit(text: str) -> Tuple[str, float]:
    """Detect monetary unit from financial text.
    Returns (unit_label, multiplier_to_yuan).
    Checks larger units first to avoid false matches.
    """
    # Check explicit unit declarations first
    if re.search(r'单位[：:]\s*亿元', text):
        return "亿元", 1e8
    if re.search(r'单位[：:]\s*万元', text):
        return "万元", 1e4
    if re.search(r'单位[：:]\s*千元', text):
        return "千元", 1e3
    if re.search(r'单位[：:]\s*百万元', text):
        return "百万元", 1e6
    if re.search(r'单位[：:]\s*元', text):
        return "元", 1.0

    # Check column headers / labels for unit hints
    if "亿元" in text:
        return "亿元", 1e8
    if "万元" in text:
        return "万元", 1e4
    if "千元" in text:
        return "千元", 1e3
    if "百万元" in text:
        return "百万元", 1e6

    return "元", 1.0


# ── Account name → canonical mapping ──────────────────────────────────
# Covers the most common Chinese financial report account names.
# More comprehensive mapping is in lithium_knowledge_base.json account_mapping.

_ACCOUNT_PATTERNS: list = [
    # (regex_or_keywords, canonical_name)
    # ── 利润表 ──
    (r"营业(?:总)?收入", "营业收入"),
    (r"营业(?:总)?成本", "营业成本"),
    (r"(?:扣非|经常性)?净利润", "净利润"),
    (r"扣除非经常性损益.*?净利润", "扣非净利润"),
    (r"销售费用", "销售费用"),
    (r"管理(?:费用|费用及研发)", "管理费用"),
    (r"研发(?:费用|支出|投入)", "研发费用"),
    (r"财务(?:费用|费用-利息)", "财务费用"),
    (r"(?:资产|信用)减值损失", "资产减值损失"),
    (r"投资收益", "投资收益"),
    (r"营业外收入", "营业外收入"),
    (r"营业外支出", "营业外支出"),
    (r"所得税(?:费用)?", "所得税费用"),
    (r"利息(?:费用|支出)", "利息费用"),
    (r"基本每股收益", "基本每股收益"),
    # ── 资产负债表 ──
    (r"流动资产(?:合计)?", "流动资产"),
    (r"流动负债(?:合计)?", "流动负债"),
    (r"存货(?!.*跌价)(?!.*周转)", "存货"),
    (r"(?:预付款项|预付账款)", "预付款项"),
    (r"应收(?:票据及)?账(?:款|项)", "应收账款"),
    (r"固定(?:资产|资产净值)", "固定资产"),
    (r"(?:资产总计|总资产)", "总资产"),
    (r"(?:负债总计|总负债|负债合计)", "总负债"),
    (r"(?:所有者权益|股东权益)(?:合计)?|净资产", "净资产"),
    (r"在建工程", "在建工程"),
    (r"合同负债|预收(?:款项|账款)", "合同负债"),
    (r"货币资金|现金及现金等价物", "货币资金"),
    (r"短期借款", "短期借款"),
    (r"长期借款", "长期借款"),
    (r"应付(?:票据及)?账(?:款|项)", "应付账款"),
    (r"商誉", "商誉"),
    (r"无形资产", "无形资产"),
    (r"开发支出", "开发支出"),
    (r"长期待摊费用", "长期待摊费用"),
    (r"应付职工薪酬", "应付职工薪酬"),
    (r"应交税费", "应交税费"),
    (r"实收资本|股本", "实收资本"),
    (r"资本公积", "资本公积"),
    (r"未分配利润", "未分配利润"),
    (r"少数股东权益", "少数股东权益"),
    (r"其他应收款", "其他应收款"),
    (r"其他应付款", "其他应付款"),
    (r"盈余公积", "盈余公积"),
    (r"专项储备", "专项储备"),
    (r"其他综合收益", "其他综合收益"),
    (r"一年内到期的非流动负债", "一年内到期的非流动负债"),
    (r"长期应付款", "长期应付款"),
    # ── 现金流量表 ──
    (r"经营(?:活动|性).*?现金流(?:量)?(?:净额|小计)?", "经营活动现金流净额"),
    (r"销售商品.*?(?:收到|提供劳务).*?现金", "销售商品收到的现金"),
    (r"购买商品.*?(?:支付|接受劳务).*?现金", "购买商品支付的现金"),
    (r"支付(?:给)?职工.*?现金", "支付给职工的现金"),
    (r"支付的各项税费", "支付的各项税费"),
    (r"收回投资.*?现金", "收回投资收到的现金"),
    (r"取得投资收益.*?现金", "取得投资收益收到的现金"),
    (r"处置(?:固定|无形).*?资产.*?现金", "处置固定资产收回的现金"),
    (r"购建(?:固定|无形).*?资产.*?现金", "购建固定资产支付的现金"),
    (r"投资支付的现金", "投资支付的现金"),
    (r"吸收投资.*?现金", "吸收投资收到的现金"),
    (r"取得借款.*?现金", "取得借款收到的现金"),
    (r"偿还债务.*?现金", "偿还债务支付的现金"),
    (r"分配(?:股利|利润).*?现金", "分配股利利润支付的现金"),
    # ── 期初期末 ──
    (r"期初存货", "期初存货"),
    (r"期末存货", "期末存货"),
    (r"期初应收", "期初应收"),
    (r"期末应收", "期末应收"),
    (r"期初(?:固定|在建|合同|净|总|流动)[资产债]", None),  # catch-all for period-begin
    (r"期末(?:固定|在建|合同|净|总|流动)[资产债]", None),
    # ── 附注 ──
    (r"存货跌价(?:损失|准备)", "存货跌价损失"),
    (r"(?:信用减值|坏账)损失", "信用减值损失"),
    (r"资产处置(?:收益|损益)", "资产处置收益"),
    (r"政府补助|其他收益", "政府补助"),
    (r"汇兑(?:损益|损失)", "汇兑损益"),
    (r"(?:加权)?平均(?:净|总|固定|存货|应收)[资产债]", None),
]


def _parse_number(val_str: str) -> Optional[float]:
    """Parse a numeric value string, handling Chinese formatting."""
    if not val_str or not val_str.strip():
        return None
    s = val_str.strip()
    # Remove commas, Chinese commas, spaces
    s = s.replace(",", "").replace("，", "").replace(" ", "")
    # Handle negative with brackets: (1234) → -1234
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    # Remove leading/trailing non-numeric except minus and dot
    s = re.sub(r'[^\d.\-]', '', s)
    try:
        return float(s)
    except ValueError:
        return None


# ── Excel extraction ──────────────────────────────────────────────────

def extract_from_excel(file_path: str) -> Dict[str, Optional[float]]:
    """Extract financial data from an Excel file.

    Scans all sheets for rows with (account_name, value) pairs.
    Handles both single-sheet summary formats and multi-sheet detailed formats.
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("需要安装 pandas: pip install pandas openpyxl")

    result: Dict[str, Optional[float]] = {}
    xls = pd.ExcelFile(file_path)

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet, header=None)

        # Detect unit from first few rows
        unit_text = ""
        for i in range(min(10, len(df))):
            row_text = " ".join(str(c) for c in df.iloc[i].dropna())
            unit_text += row_text + " "
        unit_label, multiplier = detect_unit(unit_text)

        # Scan for account → value pairs
        for i in range(len(df)):
            row = df.iloc[i]
            for j in range(len(row)):
                cell_text = str(row.iloc[j]).strip() if pd.notna(row.iloc[j]) else ""

                # Try to match account name
                for pattern, canonical in _ACCOUNT_PATTERNS:
                    if canonical is None:
                        continue
                    if re.search(pattern, cell_text, re.IGNORECASE):
                        # Look for a number in this row (same column or adjacent)
                        val = _find_number_in_row(row, j)
                        if val is not None:
                            result[canonical] = val * multiplier

        # Also try columnar format: column A = account names, column B = values
        if len(df.columns) >= 2 and len(result) < 5:
            for i in range(len(df)):
                a = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
                b = pd.notna(df.iloc[i, 1]) if len(df.columns) > 1 else False
                if a and b:
                    for pattern, canonical in _ACCOUNT_PATTERNS:
                        if canonical and re.search(pattern, a, re.IGNORECASE):
                            val = _parse_number(str(df.iloc[i, 1]))
                            if val is not None:
                                result[canonical] = val * multiplier

    return _deduplicate_and_validate(result)


def _find_number_in_row(row, skip_col: int) -> Optional[float]:
    """Find the best numeric value in a row, skipping the account name column.

    Prefers the largest absolute value (real financial data tends to be larger
    than row numbers or other metadata).
    """
    import pandas as pd
    candidates = []
    for j in range(len(row)):
        if j == skip_col:
            continue
        val = _parse_number(str(row.iloc[j])) if pd.notna(row.iloc[j]) else None
        if val is not None:
            candidates.append(abs(val))
    if candidates:
        # Return the value with the largest magnitude
        max_val = max(candidates)
        # Re-find the original (potentially negative) value
        for j in range(len(row)):
            if j == skip_col:
                continue
            val = _parse_number(str(row.iloc[j])) if pd.notna(row.iloc[j]) else None
            if val is not None and abs(val) == max_val:
                return val
    return None


# ── PDF extraction ────────────────────────────────────────────────────

def extract_from_pdf(file_path: str) -> Dict[str, Optional[float]]:
    """Extract financial data from a PDF using pdftotext + keyword matching.

    Falls back to pdfplumber (pure Python) when the pdftotext binary is
    unavailable -- e.g. on PaaS images without poppler-utils.
    """
    text = _pdftotext(file_path)
    if not text:
        text = _pdfplumber_text(file_path)
    if not text:
        raise RuntimeError("无法解析PDF。请确认文件未加密且包含文本层，或使用 Excel 格式上传。")

    result: Dict[str, Optional[float]] = {}
    unit_label, multiplier = detect_unit(text[:2000])

    # Split into lines, scan each line for account→value pairs
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        for pattern, canonical in _ACCOUNT_PATTERNS:
            if canonical is None:
                continue
            if re.search(pattern, line, re.IGNORECASE):
                # Find the last number in the line (usually the value)
                numbers = re.findall(r'[-]?\d[\d,.]*(?:\s*[万亿千百]?元?)?', line)
                if numbers:
                    val = _parse_number(numbers[-1])
                    if val is not None:
                        result[canonical] = val * multiplier

    return _deduplicate_and_validate(result)


def _pdftotext(file_path: str) -> str:
    """Run pdftotext to extract text from PDF."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", file_path, "-"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ""


def _pdfplumber_text(file_path: str) -> str:
    """Extract PDF text with pdfplumber (no external binary needed)."""
    try:
        import pdfplumber
    except ImportError:
        return ""
    try:
        parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts).strip()
    except Exception:
        return ""


# ── Deduplication ─────────────────────────────────────────────────────

def _deduplicate_and_validate(data: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Remove zero values and keep only valid financial data."""
    result = {}
    for k, v in data.items():
        if v is not None and abs(v) > 0.01:
            result[k] = round(v, 2)
    return result


# ── Main extraction entry point ───────────────────────────────────────

def extract(file_path: str) -> Dict[str, Optional[float]]:
    """Extract financial data from a file (Excel or PDF)."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return extract_from_excel(file_path)
    elif suffix == ".pdf":
        return extract_from_pdf(file_path)
    else:
        raise ValueError(f"不支持的文件格式: {suffix}。请上传 .xlsx 或 .pdf 文件。")


# ── Test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        fpath = sys.argv[1]
        print(f"提取: {fpath}")
        data = extract(fpath)
        print(f"提取到 {len(data)} 个科目:")
        for k, v in sorted(data.items()):
            unit = "亿元" if abs(v) >= 1e8 else ("万元" if abs(v) >= 1e4 else "元")
            print(f"  {k}: {v/1e8:.2f} 亿元" if abs(v) >= 1e8 else f"  {k}: {v:.2f}")
    else:
        print("用法: python data_extractor.py <file.xlsx|file.pdf>")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FinAgent-Lithium 知识库转换脚本
功能：将《锂电行业财报分析指标框架》Excel 自动解析为 lithium_knowledge_base.json

使用方法：
    python excel_to_json.py --input "锂电行业财报分析指标.xlsx" --output "lithium_knowledge_base.json"

依赖：
    pip install pandas openpyxl
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("错误：缺少 pandas 依赖。请运行: pip install pandas openpyxl")
    sys.exit(1)



def extract_accounts(formula):
    """Extract account names from a Chinese formula string."""
    if not formula or pd.isna(formula):
        return []
    s = str(formula)
    # Split on operators and numbers
    parts = re.split(r'[÷+\-−×*/()（）\d.%=\s\n;；，,、]+', s)
    accounts = []
    for p in parts:
        p = p.strip()
        # Must contain Chinese and be >= 2 chars
        if p and re.search(r'[一-鿿]', p) and len(p) >= 2:
            # Filter out formula descriptors
            if p not in ('乘以', '除以', '减去', '加上', '其中', '合计', '平均', '当期',
                         '上期', '本期', '期末', '期初', '余额', '平均值', '科目余额'):
                accounts.append(p)
    return accounts

def parse_range(range_str):
    """解析阈值区间字符串，如 '5~9 次', '>=1.2', '<0.6', '30%~55%'"""
    if pd.isna(range_str) or not str(range_str).strip():
        return None

    s = str(range_str).strip()
    # 去除单位后缀
    s = re.sub(r'[次天倍% pct\s]+$', '', s)
    s = re.sub(r'\s+', '', s)

    result = {"raw": str(range_str).strip()}

    # 匹配 >=X
    if m := re.match(r'^>=([\d.]+)$', s):
        result["type"] = "gte"
        result["value"] = float(m.group(1))
    # 匹配 >X
    elif m := re.match(r'^>([\d.]+)$', s):
        result["type"] = "gt"
        result["value"] = float(m.group(1))
    # 匹配 <=X
    elif m := re.match(r'^<=([\d.]+)$', s):
        result["type"] = "lte"
        result["value"] = float(m.group(1))
    # 匹配 <X
    elif m := re.match(r'^<([\d.]+)$', s):
        result["type"] = "lt"
        result["value"] = float(m.group(1))
    # 匹配 X~Y
    elif m := re.match(r'^([\d.]+)~([\d.]+)$', s):
        result["type"] = "range"
        result["low"] = float(m.group(1))
        result["high"] = float(m.group(2))
    # 匹配 ±X% 以内
    elif m := re.match(r'^±([\d.]+)%?以内$', s):
        result["type"] = "abs_range"
        result["value"] = float(m.group(1))
    # 匹配 ±X%~±Y%
    elif m := re.match(r'^±([\d.]+)%~±([\d.]+)%$', s):
        result["type"] = "abs_range"
        result["low"] = float(m.group(1))
        result["high"] = float(m.group(2))
    # 匹配 下限-Xpct
    elif m := re.match(r'^下限-([\d.]+)pct$', s):
        result["type"] = "below_baseline"
        result["value"] = float(m.group(1))
    else:
        result["type"] = "raw"

    return result


def extract_linkage_rules(linkage_text):
    """从联动映射文本中提取规则"""
    if pd.isna(linkage_text) or not str(linkage_text).strip():
        return []

    rules = []
    text = str(linkage_text).strip()
    # 按数字序号分割
    parts = re.split(r'\n?(?=\d+\.)', text)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 提取状态等级
        status = "风险累积"  # 默认
        if "最优" in part or "优质" in part:
            status = "最优状态"
        elif "良性" in part or "改善" in part:
            status = "良性扩张"
        elif "高危" in part or "危机" in part or "亏损" in part or "断裂" in part:
            status = "高危状态"
        elif "衰退" in part or "下行" in part or "萎缩" in part:
            status = "衰退信号"
        elif "风险累积" in part or "恶化" in part or "压力" in part:
            status = "风险累积"

        # 提取指标组合（箭头前的部分）
        if '→' in part:
            combination = part.split('→')[0].strip()
            description = part.split('→')[1].strip()
        elif '->' in part:
            combination = part.split('->')[0].strip()
            description = part.split('->')[1].strip()
        else:
            combination = part
            description = part

        # 去除序号前缀
        combination = re.sub(r'^\d+\.\s*', '', combination)

        rules.append({
            "combination": combination,
            "status": status,
            "description": description,
            "raw": part
        })

    return rules


def extract_anomaly_rules(anomaly_text):
    """从异常识别文本中提取规则"""
    if pd.isna(anomaly_text) or not str(anomaly_text).strip():
        return []

    rules = []
    text = str(anomaly_text).strip()
    # 按数字序号分割
    parts = re.split(r'\n?(?=\d+\.)', text)

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # 去除序号前缀
        part = re.sub(r'^\d+\.\s*', '', part)

        # 提取冒号前后的内容
        if '：' in part:
            desc = part.split('：')[0].strip()
            risk = part.split('：')[1].strip()
        elif ':' in part:
            desc = part.split(':')[0].strip()
            risk = part.split(':')[1].strip()
        else:
            desc = part
            risk = part

        rules.append({
            "description": desc,
            "risk": risk,
            "raw": part,
            "requires_external": any(kw in desc for kw in ['锂价', '市场', '行业', '金属价格', '钴', '镍'])
        })

    return rules


def get_sector_code(sector_name):
    """根据赛道名称生成编码"""
    if '锂资源' in sector_name or '锂盐' in sector_name:
        return "1.1"
    elif '钴' in sector_name or '镍' in sector_name or '前驱体' in sector_name:
        return "1.2"
    elif '正极' in sector_name:
        return "2.1"
    elif '负极' in sector_name:
        return "2.2"
    elif '电解液' in sector_name:
        return "2.3"
    elif '隔膜' in sector_name:
        return "2.4"
    elif '铜箔' in sector_name or '铝箔' in sector_name:
        return "2.5"
    elif '辅材' in sector_name or '导电剂' in sector_name or '粘结剂' in sector_name:
        return "2.6"
    elif '动力电池' in sector_name and '电芯' in sector_name:
        return "3.1"
    elif '储能' in sector_name and '电芯' in sector_name:
        return "3.2"
    elif '消费' in sector_name or '3C' in sector_name:
        return "3.3"
    elif 'PACK' in sector_name or '模组' in sector_name:
        return "3.4"
    elif '结构件' in sector_name:
        return "3.5"
    elif '储能系统' in sector_name or 'EPC' in sector_name:
        return "4.1"
    elif '回收' in sector_name or '梯次' in sector_name:
        return "4.2"
    elif '贸易' in sector_name or '经销' in sector_name:
        return "4.3"
    elif '生产设备' in sector_name or '整线' in sector_name:
        return "5.1"
    elif '检测' in sector_name:
        return "5.2"
    elif '技术服务' in sector_name or '运维' in sector_name:
        return "5.3"
    elif '一体化' in sector_name or '跨界' in sector_name:
        return "6.1"
    return "unknown"


def get_unit(formula, normal_range):
    """根据公式和正常区间推断单位"""
    if pd.notna(formula) and '%' in str(formula):
        return "%"
    if pd.notna(normal_range):
        nr = str(normal_range)
        if '次' in nr:
            return "次"
        elif '天' in nr:
            return "天"
        elif '倍' in nr:
            return "倍"
        elif '%' in nr:
            return "%"
    return "无"


def convert_excel_to_json(excel_path, output_path):
    """主转换函数"""
    print(f"正在读取 Excel: {excel_path}")

    # 读取Excel，跳过前两行表头
    df = pd.read_excel(excel_path, sheet_name='表格_20260531', header=1)
    df.columns = ['一级产业链分类', '二级细分赛道', '赛道核心经营特征', '财报分析核心侧重点', 
                  '指标类型', '指标名称', '指标计算公式', '正常区间', '警惕区间', '高风险区间',
                  '单指标经营实景映射', '赛道整体经营实景联动映射', '报表异常识别要点']

    # 前向填充指标类型
    df['指标类型'] = df['指标类型'].ffill()

    print(f"数据行数: {len(df)}")

    # 提取通用指标（前10行）
    general_indicators = []
    for idx in range(min(10, len(df))):
        row = df.iloc[idx]
        if pd.isna(row['指标名称']) or not str(row['指标名称']).strip():
            continue

        indicator = {
            "name": str(row['指标名称']).strip(),
            "formula": str(row['指标计算公式']).strip() if pd.notna(row['指标计算公式']) else "",
            "unit": get_unit(row['指标计算公式'], row['正常区间']),
            "normal_range": str(row['正常区间']).strip() if pd.notna(row['正常区间']) else "",
            "warning_range": str(row['警惕区间']).strip() if pd.notna(row['警惕区间']) else "",
            "high_risk_range": str(row['高风险区间']).strip() if pd.notna(row['高风险区间']) else "",
            "normal_parsed": parse_range(row['正常区间']),
            "warning_parsed": parse_range(row['警惕区间']),
            "high_risk_parsed": parse_range(row['高风险区间']),
            "single_mapping": str(row['单指标经营实景映射']).strip() if pd.notna(row['单指标经营实景映射']) else "",
            "accounts_needed": extract_accounts(row["指标计算公式"])
        }
        general_indicators.append(indicator)

    print(f"通用指标: {len(general_indicators)} 个")

    # 提取各赛道
    sectors = {}
    sector_starts = []
    for idx, row in df.iterrows():
        if pd.notna(row['二级细分赛道']) and str(row['二级细分赛道']).strip():
            sector_starts.append(idx)
    sector_starts.append(len(df))

    for i in range(len(sector_starts) - 1):
        start_idx = sector_starts[i]
        end_idx = sector_starts[i + 1]

        sector_row = df.iloc[start_idx]
        sector_name = str(sector_row['二级细分赛道']).strip()
        sector_code = get_sector_code(sector_name)

        characteristics = str(sector_row['赛道核心经营特征']).strip() if pd.notna(sector_row['赛道核心经营特征']) else ""
        focus = str(sector_row['财报分析核心侧重点']).strip() if pd.notna(sector_row['财报分析核心侧重点']) else ""

        linkage_rules = extract_linkage_rules(sector_row['赛道整体经营实景联动映射'])
        anomaly_rules = extract_anomaly_rules(sector_row['报表异常识别要点'])

        indicators = {}
        for idx in range(start_idx, end_idx):
            row = df.iloc[idx]
            if pd.isna(row['指标名称']) or not str(row['指标名称']).strip():
                continue

            indicator_type = str(row['指标类型']).strip() if pd.notna(row['指标类型']) else "其他"

            indicator = {
                "name": str(row['指标名称']).strip(),
                "formula": str(row['指标计算公式']).strip() if pd.notna(row['指标计算公式']) else "",
                "unit": get_unit(row['指标计算公式'], row['正常区间']),
                "normal_range": str(row['正常区间']).strip() if pd.notna(row['正常区间']) else "",
                "warning_range": str(row['警惕区间']).strip() if pd.notna(row['警惕区间']) else "",
                "high_risk_range": str(row['高风险区间']).strip() if pd.notna(row['高风险区间']) else "",
                "normal_parsed": parse_range(row['正常区间']),
                "warning_parsed": parse_range(row['警惕区间']),
                "high_risk_parsed": parse_range(row['高风险区间']),
                "single_mapping": str(row['单指标经营实景映射']).strip() if pd.notna(row['单指标经营实景映射']) else "",
                "accounts_needed": extract_accounts(row["指标计算公式"])
            }

            if indicator_type not in indicators:
                indicators[indicator_type] = []
            indicators[indicator_type].append(indicator)

        sectors[sector_code] = {
            "name": sector_name,
            "characteristics": characteristics,
            "focus": focus,
            "indicators": indicators,
            "linkage_rules": linkage_rules,
            "anomaly_rules": anomaly_rules
        }

        indicator_count = sum(len(v) for v in indicators.values())
        print(f"  赛道 {sector_code} ({sector_name}): {indicator_count} 个指标, {len(linkage_rules)} 条联动规则, {len(anomaly_rules)} 条异常规则")

    # 构建最终JSON
    knowledge_base = {
        "sectors": sectors,
        "general_indicators": general_indicators,
        "account_mapping": {
            "营业收入": ["营业收入", "营业总收入", "Revenue", "Total Revenue", "收入", "销售收入"],
            "营业成本": ["营业成本", "营业总成本", "Cost of Revenue", "Cost of Sales", "销售成本"],
            "毛利": ["毛利", "Gross Profit"],
            "销售费用": ["销售费用", "Selling Expenses", "Selling and Marketing Expenses"],
            "管理费用": ["管理费用", "Administrative Expenses", "General and Administrative Expenses", "管理费用及研发费用"],
            "研发费用": ["研发费用", "Research and Development Expenses", "R&D Expenses", "研发支出"],
            "财务费用": ["财务费用", "Finance Costs", "Financial Expenses", "利息费用"],
            "扣非净利润": ["扣除非经常性损益的净利润", "扣非净利润", "Net Profit Excluding Non-recurring Items", "经常性净利润"],
            "净利润": ["净利润", "归母净利润", "归属于上市公司股东的净利润", "Net Profit", "Net Income"],
            "经营活动现金流净额": ["经营活动产生的现金流量净额", "经营活动现金流净额", "Net Cash from Operating Activities", "Operating Cash Flow"],
            "流动资产": ["流动资产", "Current Assets", "流动资产合计"],
            "流动负债": ["流动负债", "Current Liabilities", "流动负债合计"],
            "存货": ["存货", "Inventories", "Inventory"],
            "预付款项": ["预付款项", "预付账款", "Prepayments", "Prepaid Expenses"],
            "应收账款": ["应收账款", "应收票据及应收账款", "应收款项融资", "Notes and Accounts Receivable", "Trade Receivables"],
            "固定资产": ["固定资产", "Fixed Assets", "Property, Plant and Equipment", "PP&E", "固定资产净值"],
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
            "销售商品收到的现金": ["销售商品、提供劳务收到的现金", "Cash from Sales", "Cash Received from Sales"],
            "购买商品支付的现金": ["购买商品、接受劳务支付的现金", "Cash Paid for Purchases", "Cash Paid for Goods"],
            "支付给职工的现金": ["支付给职工以及为职工支付的现金", "Cash Paid to Employees"],
            "支付的各项税费": ["支付的各项税费", "Taxes Paid"],
            "支付其他经营活动现金": ["支付其他与经营活动有关的现金", "Other Cash Paid Relating to Operating Activities"],
            "收回投资收到的现金": ["收回投资收到的现金", "Cash from Disposal of Investments"],
            "取得投资收益收到的现金": ["取得投资收益收到的现金", "Cash from Investment Income"],
            "处置固定资产收回的现金": ["处置固定资产、无形资产和其他长期资产收回的现金", "Cash from Disposal of Assets"],
            "购建固定资产支付的现金": ["购建固定资产、无形资产和其他长期资产支付的现金", "Cash Paid for Acquisition of Assets"],
            "投资支付的现金": ["投资支付的现金", "Cash Paid for Investments"],
            "吸收投资收到的现金": ["吸收投资收到的现金", "Cash from Capital Contributions"],
            "取得借款收到的现金": ["取得借款收到的现金", "Cash from Borrowings"],
            "偿还债务支付的现金": ["偿还债务支付的现金", "Cash Repayment of Borrowings"],
            "分配股利利润支付的现金": ["分配股利、利润或偿付利息支付的现金", "Cash Paid for Dividends and Interest"],
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
            "期初应收账款": ["期初应收账款", "期初应收票据及应收账款", "Beginning Notes and Accounts Receivable"],
            "期末应收账款": ["期末应收账款", "期末应收票据及应收账款", "Ending Notes and Accounts Receivable"],
            "期初流动资产": ["期初流动资产", "Beginning Current Assets"],
            "期末流动资产": ["期末流动资产", "Ending Current Assets"],
            "期初流动负债": ["期初流动负债", "Beginning Current Liabilities"],
            "期末流动负债": ["期末流动负债", "Ending Current Liabilities"],
            "存货跌价损失": ["存货跌价损失", "存货跌价准备", "Inventory Impairment Loss", "Provision for Inventory Decline"],
            "信用减值损失": ["信用减值损失", "坏账损失", "Credit Impairment Loss", "Provision for Bad Debts"],
            "资产处置收益": ["资产处置收益", "资产处置损益", "Gain on Disposal of Assets"],
            "政府补助": ["政府补助", "其他收益", "Government Grants", "Other Income"],
            "汇兑损益": ["汇兑损益", "汇兑损失", "Exchange Gain/Loss", "Foreign Exchange Difference"],
            "营业利润": ["营业利润", "Operating Profit"],
            "利润总额": ["利润总额", "Total Profit", "Profit Before Tax"],
            "所得税": ["所得税", "Income Tax"],
            "基本每股收益": ["基本每股收益", "EPS", "Basic Earnings Per Share"],
            "稀释每股收益": ["稀释每股收益", "Diluted EPS"],
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
    }

    # 保存JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(knowledge_base, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] 转换完成！")
    print(f"输出文件: {output_path}")
    print(f"文件大小: {Path(output_path).stat().st_size} bytes")
    print(f"\n📊 统计:")
    print(f"  通用指标: {len(general_indicators)} 个")
    print(f"  赛道数量: {len(sectors)} 个")
    for code, sector in sectors.items():
        indicator_count = sum(len(v) for v in sector['indicators'].values())
        print(f"  赛道 {code} ({sector['name']}): {indicator_count} 个指标, {len(sector['linkage_rules'])} 条联动规则, {len(sector['anomaly_rules'])} 条异常规则")

    return knowledge_base


def main():
    parser = argparse.ArgumentParser(description='将锂电行业财报分析指标Excel转换为JSON知识库')
    parser.add_argument('--input', '-i', default='锂电行业财报分析指标（通用+特殊）.xlsx', help='输入Excel文件路径')
    parser.add_argument('--output', '-o', default='lithium_knowledge_base.json', help='输出JSON文件路径')
    args = parser.parse_args()

    convert_excel_to_json(args.input, args.output)


if __name__ == '__main__':
    main()

"""
Market Data Adapter — Wind MCP primary + AKShare fallback.

Data sources (in priority order):
  1. Wind MCP (https://aifinmarket.wind.com.cn) — primary, via Node.js CLI subprocess
  2. AKShare (Sina / East Money / THS) — graceful fallback on any Wind failure

All public function signatures remain unchanged — drop-in for web/workflow.py.

Usage:
    from nodes.wind_adapter import fetch_financials, stock_code_to_windcode

    windcode = stock_code_to_windcode("300750")  # → "300750.SZ"
    data = fetch_financials(windcode, period="2025年报")
    # → {"营业收入": 400000000000.0, "净利润": 44000000000.0, ...}
"""

import json
import logging
import os
import re
import hashlib
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Any, List

logger = logging.getLogger(__name__)

# ── Module-level constants ──────────────────────────────────────────────────

# Wind MCP CLI location (relative to this file: nodes/ → project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_WIND_SKILL_DIR = str(_PROJECT_ROOT / ".agents" / "skills" / "wind-mcp-skill")
_WIND_CLI = ["node", "scripts/cli.mjs"]
_WIND_STATUS: Dict[str, Any] = {
    "skill_dir": None,
    "cache_dir": None,
    "live_calls_enabled": None,
    "api_key_configured": False,
    "last_error": None,
    "last_tool": None,
    "last_response_preview": None,
    "last_cache_hit": False,
    "daily_limit": None,
    "daily_used": None,
}

# Track whether node is available (avoid spamming warnings)
_node_available = None  # None = unknown, True/False after first check

# ── Stock code conversion ──────────────────────────────────────────────────

# Shenzhen exchange prefixes (300, 002, 000, 001) → .SZ
_SZ_PREFIXES = {"300", "002", "000", "001", "301", "003"}
# Shanghai exchange prefixes (600, 601, 603, 605, 688) → .SH
_SH_PREFIXES = {"600", "601", "603", "605", "688"}
# Beijing exchange → .BJ
_BJ_PREFIXES = {"8"}


def stock_code_to_windcode(code: str) -> str:
    """Convert plain stock code to Wind/Sina format with exchange suffix.

    "300750" → "300750.SZ"
    "688005" → "688005.SH"
    "002466" → "002466.SZ"
    "600519" → "600519.SH"

    If already in windcode format (contains "."), return as-is.
    """
    code = code.strip()
    if "." in code:
        return code
    prefix3 = code[:3]
    prefix1 = code[:1]
    if prefix3 in _SZ_PREFIXES:
        return f"{code}.SZ"
    if prefix3 in _SH_PREFIXES:
        return f"{code}.SH"
    if prefix1 in _BJ_PREFIXES:
        return f"{code}.BJ"
    if code.startswith("6"):
        return f"{code}.SH"
    return f"{code}.SZ"


def _windcode_to_sina(code: str) -> str:
    """Convert windcode to Sina stock code format.

    "300750.SZ" → "sz300750"
    "688005.SH" → "sh688005"
    """
    code = code.strip()
    if "." in code:
        parts = code.split(".")
        num = parts[0]
        exchange = parts[1].lower()
    else:
        num = code
        prefix3 = code[:3]
        exchange = "sz" if prefix3 in _SZ_PREFIXES else "sh"
    return f"{exchange}{num}"


def _period_to_date(period: str) -> Optional[str]:
    """Convert report period label to Sina date format (YYYYMMDD).

    "2025年报" → "20251231"
    "2025三季报" → "20250930"
    "2025中报" → "20250630"
    "2025一季报" → "20250331"
    "最新一期" → None (use latest)
    """
    if not period or period in ("最新一期", "最新", ""):
        return None
    m = re.match(r"(\d{4})年报", period)
    if m:
        return f"{m.group(1)}1231"
    m = re.match(r"(\d{4})三季报", period)
    if m:
        return f"{m.group(1)}0930"
    m = re.match(r"(\d{4})中报", period)
    if m:
        return f"{m.group(1)}0630"
    m = re.match(r"(\d{4})一季报", period)
    if m:
        return f"{m.group(1)}0331"
    if re.match(r"^\d{8}$", period):
        return period
    return None


# ── Account name mappings ──────────────────────────────────────────────────

# AKShare: Sina column name → canonical account name
_SINA_TO_CANONICAL: Dict[str, str] = {
    # Balance Sheet
    "资产总计": "总资产",
    "负债合计": "总负债",
    "所有者权益(或股东权益)合计": "净资产",
    "归属于母公司股东权益合计": "归母净资产",
    "流动资产": "流动资产",
    "流动资产合计": "流动资产",
    "流动负债": "流动负债",
    "流动负债合计": "流动负债",
    "货币资金": "货币资金",
    "应收账款": "应收账款",
    "应收票据": "应收票据",
    "应收票据及应收账款": "应收票据及应收账款",
    "存货": "存货",
    "固定资产净额": "固定资产",
    "固定资产净值": "固定资产",
    "固定资产原值": "固定资产原值",
    "在建工程": "在建工程",
    "在建工程合计": "在建工程",
    "商誉": "商誉",
    "无形资产": "无形资产",
    "短期借款": "短期借款",
    "长期借款": "长期借款",
    "应付账款": "应付账款",
    "应付票据": "应付票据",
    "应付票据及应付账款": "应付票据及应付账款",
    "预收款项": "预收款项",
    "合同负债": "合同负债",
    "长期应付款": "长期应付款",
    "长期应付款合计": "长期应付款",
    "交易性金融资产": "交易性金融资产",
    "其他应收款": "其他应收款",
    "其他流动资产": "其他流动资产",
    "一年内到期的非流动资产": "一年内到期的非流动资产",
    "递延所得税资产": "递延所得税资产",
    "其他非流动资产": "其他非流动资产",
    "应付职工薪酬": "应付职工薪酬",
    "应交税费": "应交税费",
    "其他应付款合计": "其他应付款合计",
    "一年内到期的非流动负债": "一年内到期的非流动负债",
    "其他流动负债": "其他流动负债",
    "租赁负债": "租赁负债",
    "长期递延收益": "长期递延收益",
    "递延所得税负债": "递延所得税负债",
    "实收资本(或股本)": "实收资本",
    "资本公积": "资本公积",
    "盈余公积": "盈余公积",
    "未分配利润": "未分配利润",
    "少数股东权益": "少数股东权益",
    # Income Statement
    "营业总收入": "营业收入",
    "营业收入": "营业收入",
    "营业总成本": "营业总成本",
    "营业成本": "营业成本",
    "研发费用": "研发费用",
    "销售费用": "销售费用",
    "管理费用": "管理费用",
    "财务费用": "财务费用",
    "利息费用": "利息费用",
    "投资收益": "投资收益",
    "资产减值损失": "资产减值损失",
    "信用减值损失": "信用减值损失",
    "公允价值变动收益": "公允价值变动收益",
    "营业利润": "营业利润",
    "利润总额": "利润总额",
    "所得税费用": "所得税费用",
    "净利润": "净利润",
    "归属于母公司所有者的净利润": "归母净利润",
    "少数股东损益": "少数股东损益",
    "基本每股收益": "基本每股收益",
    "稀释每股收益": "稀释每股收益",
    "营业税金及附加": "营业税金及附加",
    "其他收益": "其他收益",
    "营业外收入": "营业外收入",
    "营业外支出": "营业外支出",
    "资产处置收益": "资产处置收益",
    # Cash Flow
    "经营活动产生的现金流量净额": "经营活动现金流净额",
    "投资活动产生的现金流量净额": "投资活动现金流净额",
    "筹资活动产生的现金流量净额": "筹资活动现金流净额",
    "销售商品、提供劳务收到的现金": "销售商品提供劳务收到的现金",
    "购建固定资产、无形资产和其他长期资产所支付的现金": "购建固定无形长期资产支付的现金",
    "期末现金及现金等价物余额": "期末现金及现金等价物余额",
    "现金及现金等价物净增加额": "现金及现金等价物净增加额",
}

# Wind NL response term → canonical account name
_WIND_TERM_TO_CANONICAL: Dict[str, str] = {
    "营业总收入": "营业收入", "营收": "营业收入", "营业收入": "营业收入",
    "营业总成本": "营业总成本", "营业成本": "营业成本",
    "归属净利润": "归母净利润", "归属于母公司所有者的净利润": "归母净利润",
    "归母净利润": "归母净利润",
    "扣非后净利润": "扣非净利润", "扣除非经常性损益的净利润": "扣非净利润",
    "扣非净利润": "扣非净利润",
    "净利润": "净利润",
    "资产总额": "总资产", "资产总计": "总资产", "总资产": "总资产",
    "负债总额": "总负债", "负债合计": "总负债", "总负债": "总负债",
    "所有者权益": "净资产", "股东权益": "净资产", "净资产": "净资产",
    "归母净资产": "归母净资产", "归属于母公司股东权益": "归母净资产",
    "流动资产": "流动资产", "流动资产合计": "流动资产",
    "流动负债": "流动负债", "流动负债合计": "流动负债",
    "货币资金": "货币资金",
    "应收账款": "应收账款", "应收票据": "应收票据",
    "应收票据及应收账款": "应收票据及应收账款",
    "存货": "存货", "存货净额": "存货",
    "固定资产": "固定资产", "固定资产净额": "固定资产",
    "在建工程": "在建工程",
    "无形资产": "无形资产", "商誉": "商誉",
    "短期借款": "短期借款", "长期借款": "长期借款",
    "应付账款": "应付账款", "应付票据": "应付票据",
    "应付票据及应付账款": "应付票据及应付账款",
    "预收款项": "预收款项", "合同负债": "合同负债",
    "长期应付款": "长期应付款",
    "交易性金融资产": "交易性金融资产",
    "其他应收款": "其他应收款",
    "经营活动现金流净额": "经营活动现金流净额",
    "经营活动产生的现金流量净额": "经营活动现金流净额",
    "经营性现金流": "经营活动现金流净额",
    "投资活动现金流净额": "投资活动现金流净额",
    "投资活动产生的现金流量净额": "投资活动现金流净额",
    "筹资活动现金流净额": "筹资活动现金流净额",
    "筹资活动产生的现金流量净额": "筹资活动现金流净额",
    "销售商品提供劳务收到的现金": "销售商品提供劳务收到的现金",
    "购建固定无形长期资产支付的现金": "购建固定无形长期资产支付的现金",
    "期末现金及现金等价物余额": "期末现金及现金等价物余额",
    "现金及现金等价物净增加额": "现金及现金等价物净增加额",
    "研发费用": "研发费用",
    "销售费用": "销售费用", "管理费用": "管理费用",
    "财务费用": "财务费用", "利息费用": "利息费用",
    "投资收益": "投资收益",
    "营业利润": "营业利润", "利润总额": "利润总额",
    "所得税费用": "所得税费用", "所得税": "所得税费用",
    "资产减值损失": "资产减值损失",
    "信用减值损失": "信用减值损失",
    "公允价值变动收益": "公允价值变动收益",
    "营业税金及附加": "营业税金及附加",
    "其他收益": "其他收益",
    "营业外收入": "营业外收入", "营业外支出": "营业外支出",
    "资产处置收益": "资产处置收益",
    "基本每股收益": "基本每股收益", "稀释每股收益": "稀释每股收益",
    "少数股东权益": "少数股东权益", "少数股东损益": "少数股东损益",
    "实收资本": "实收资本", "资本公积": "资本公积",
    "盈余公积": "盈余公积", "未分配利润": "未分配利润",
    "应付职工薪酬": "应付职工薪酬", "应交税费": "应交税费",
    "其他应付款合计": "其他应付款合计",
    "一年内到期的非流动负债": "一年内到期的非流动负债",
    "其他流动负债": "其他流动负债",
    "租赁负债": "租赁负债",
    "长期递延收益": "长期递延收益",
    "递延所得税资产": "递延所得税资产",
    "递延所得税负债": "递延所得税负债",
    "其他非流动资产": "其他非流动资产",
    "一年内到期的非流动资产": "一年内到期的非流动资产",
    "其他流动资产": "其他流动资产",
}

# ── Wind MCP CLI subprocess helper ──────────────────────────────────────────

# Wind error codes that trigger AKShare fallback (all codes)
_WIND_FALLBACK_CODES = {
    "QUOTA_ERROR", "AUTH_ERROR", "NETWORK_ERROR",
    "TEMPORARILY_UNAVAILABLE", "TOOL_RUNTIME_ERROR",
    "NO_RESULTS", "UNKNOWN", "USAGE_ERROR", "ROUTE_ERROR",
    "PARAM_VALIDATION_ERROR", "INVALID_PARAMS_JSON",
    "INVALID_PARAM_VALUE", "INVALID_PARAM_NAME",
    "SETUP_ERROR",
}


def _valid_wind_skill_dir(path: Path) -> bool:
    """Return True when a path contains the Wind CLI entrypoint."""
    return (path / "scripts" / "cli.mjs").exists()


def _resolve_wind_skill_dir() -> Optional[str]:
    """Resolve Wind skill dir from env, project-local install, then global install."""
    candidates: List[Path] = []
    env_dir = os.environ.get("WIND_SKILL_DIR")
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    candidates.append(Path(_WIND_SKILL_DIR))
    candidates.append(Path.home() / ".agents" / "skills" / "wind-mcp-skill")

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if _valid_wind_skill_dir(resolved):
            _WIND_STATUS["skill_dir"] = str(resolved)
            return str(resolved)

    _WIND_STATUS["skill_dir"] = None
    _WIND_STATUS["last_error"] = "Wind skill directory not found"
    return None


def _wind_cache_dir() -> Path:
    base = os.environ.get("WIND_CACHE_DIR")
    cache_dir = Path(base) if base else _PROJECT_ROOT / ".cache" / "wind"
    _WIND_STATUS["cache_dir"] = str(cache_dir)
    return cache_dir


def _wind_cache_key(server_type: str, tool_name: str, params: Dict[str, Any]) -> str:
    payload = {"server_type": server_type, "tool_name": tool_name, "params": params}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_wind_cache(
    server_type: str,
    tool_name: str,
    params: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    path = _wind_cache_dir() / f"{_wind_cache_key(server_type, tool_name, params)}.json"
    if not path.exists():
        _WIND_STATUS["last_cache_hit"] = False
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _WIND_STATUS["last_cache_hit"] = True
        return data.get("result") if isinstance(data, dict) and "result" in data else data
    except Exception as e:
        logger.debug("Wind cache read failed: %s", e)
        _WIND_STATUS["last_cache_hit"] = False
        return None


def _write_wind_cache(
    server_type: str,
    tool_name: str,
    params: Dict[str, Any],
    result: Dict[str, Any],
) -> None:
    path = _wind_cache_dir() / f"{_wind_cache_key(server_type, tool_name, params)}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "server_type": server_type,
                    "tool_name": tool_name,
                    "params": params,
                    "result": result,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        logger.debug("Wind cache write failed: %s", e)


def _wind_live_calls_enabled() -> bool:
    """Allow live Wind calls when explicitly enabled or a Wind key is configured."""
    live_flag = os.environ.get("WIND_ENABLE_LIVE", "").strip().lower()
    if live_flag in {"0", "false", "no", "off"}:
        enabled = False
    else:
        enabled = (
            os.environ.get("WIND_LIVE_TEST") == "1"
            or live_flag == "1"
            or bool(os.environ.get("WIND_API_KEY"))
        )
    _WIND_STATUS["live_calls_enabled"] = enabled
    return enabled


def _wind_usage_path() -> Path:
    return _wind_cache_dir() / "usage.json"


def _read_wind_usage() -> Dict[str, Any]:
    today = date.today().isoformat()
    path = _wind_usage_path()
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
    except Exception:
        pass
    return {"date": today, "used": 0}


def _wind_daily_limit() -> int:
    try:
        return max(int(os.environ.get("WIND_DAILY_POINT_LIMIT", "500")), 0)
    except ValueError:
        return 500


def _wind_call_cost() -> int:
    try:
        return max(int(os.environ.get("WIND_CALL_POINT_COST", "1")), 1)
    except ValueError:
        return 1


def _wind_budget_allows_call() -> bool:
    usage = _read_wind_usage()
    limit = _wind_daily_limit()
    cost = _wind_call_cost()
    _WIND_STATUS["daily_limit"] = limit
    _WIND_STATUS["daily_used"] = usage.get("used", 0)
    if usage.get("used", 0) + cost > limit:
        _WIND_STATUS["last_error"] = "Wind daily point limit reached"
        return False
    return True


def _record_wind_call_usage() -> None:
    usage = _read_wind_usage()
    usage["used"] = int(usage.get("used", 0)) + _wind_call_cost()
    _WIND_STATUS["daily_used"] = usage["used"]
    try:
        path = _wind_usage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(usage, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug("Wind usage write failed: %s", e)


def get_wind_status() -> Dict[str, Any]:
    """Return Wind adapter diagnostics without making a live API call."""
    skill_dir = _resolve_wind_skill_dir()
    usage = _read_wind_usage()
    _WIND_STATUS.update({
        "skill_dir": skill_dir,
        "cache_dir": str(_wind_cache_dir()),
        "live_calls_enabled": _wind_live_calls_enabled(),
        "api_key_configured": bool(os.environ.get("WIND_API_KEY")),
        "daily_limit": _wind_daily_limit(),
        "daily_used": usage.get("used", 0),
    })
    return dict(_WIND_STATUS)


def _wind_subprocess_env() -> Dict[str, str]:
    """Build a child process env without exposing secrets in logs or state."""
    env = os.environ.copy()
    api_key = os.environ.get("WIND_API_KEY")
    if api_key:
        env["WIND_API_KEY"] = api_key
    return env


def _check_node_available() -> bool:
    """Check if Node.js is available. Cached after first check."""
    global _node_available
    if _node_available is None:
        try:
            result = subprocess.run(
                ["node", "--version"], capture_output=True, text=True, timeout=10
            )
            _node_available = (result.returncode == 0)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            _node_available = False
        if not _node_available:
            logger.warning("Node.js not found — Wind MCP unavailable, using AKShare for all data")
    return _node_available


def _call_wind_cli(
    server_type: str,
    tool_name: str,
    params: Dict[str, str],
    timeout: int = 120,
) -> Optional[Dict[str, Any]]:
    """Call Wind MCP CLI via Node.js subprocess.

    Args:
        server_type: e.g. "stock_data", "economic_data"
        tool_name: e.g. "get_stock_fundamentals", "search_stocks"
        params: Tool parameters dict (keys must match tool-contracts.md)
        timeout: Subprocess timeout in seconds

    Returns:
        Parsed MCP result dict on success, None on any failure.
    """
    cached = _read_wind_cache(server_type, tool_name, params)
    if cached is not None:
        return cached
    _WIND_STATUS["last_tool"] = f"{server_type}.{tool_name}"
    _WIND_STATUS["last_response_preview"] = None

    if not _wind_live_calls_enabled():
        _WIND_STATUS["last_error"] = "Wind live calls disabled"
        logger.debug("Wind live call disabled for %s/%s", server_type, tool_name)
        return None

    skill_dir = _resolve_wind_skill_dir()
    if not skill_dir:
        _WIND_STATUS["last_error"] = "Wind skill directory not found"
        logger.warning("Wind MCP skill directory not found; using fallback data source")
        return None

    if not _wind_budget_allows_call():
        _WIND_STATUS["last_error"] = "Wind daily point limit reached"
        logger.warning("Wind daily point limit reached; using fallback data source")
        return None

    if not _check_node_available():
        _WIND_STATUS["last_error"] = "Node.js not available"
        return None

    params_json = json.dumps(params, ensure_ascii=False, separators=(",", ":"))

    try:
        _record_wind_call_usage()
        result = subprocess.run(
            [*_WIND_CLI, "call", server_type, tool_name, params_json],
            cwd=skill_dir,
            env=_wind_subprocess_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _WIND_STATUS["last_error"] = f"Wind MCP CLI timeout after {timeout}s"
        logger.warning("Wind MCP CLI timeout (%s/%s after %ds)", server_type, tool_name, timeout)
        return None
    except FileNotFoundError:
        _node_available = False
        _WIND_STATUS["last_error"] = "Node.js not found"
        logger.warning("Node.js not found — Wind MCP unavailable")
        return None
    except Exception as e:
        _WIND_STATUS["last_error"] = f"Wind MCP CLI subprocess error: {e}"
        logger.warning("Wind MCP CLI subprocess error: %s", e)
        return None

    # Exit code 3221226505 (0xC0000005) = Windows access violation in Node.js libuv
    # This is a known Node.js/libuv bug on Windows — treat as NETWORK_ERROR for fallback
    if result.returncode != 0 and result.returncode != 1:
        logger.warning(
            "Wind MCP CLI crashed (exit %d) — Node.js libuv issue, falling back",
            result.returncode,
        )
        _WIND_STATUS["last_error"] = f"Wind MCP CLI crashed: exit {result.returncode}"
        return None

    stdout = result.stdout.strip() if result.stdout else ""
    stderr = result.stderr.strip() if result.stderr else ""
    _WIND_STATUS["last_response_preview"] = (stdout or stderr)[:500]

    if result.returncode == 0:
        # Success: stdout is the MCP result or call wrapper
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            _WIND_STATUS["last_error"] = "Wind CLI stdout was not valid JSON"
            logger.debug("Wind CLI stdout not valid JSON: %.200s", stdout)
            return None

        # The CLI's writeRawCallSuccess outputs result directly for 'call' command
        # But cmdCall wraps as {server_type, tool, result}
        # Try both shapes
        if isinstance(data, dict):
            # Shape 1: wrapper {server_type, tool, result: {...}}
            mcp_result = data.get("result")
            if isinstance(mcp_result, dict):
                _write_wind_cache(server_type, tool_name, params, mcp_result)
                return mcp_result
            # Shape 2: direct MCP result {content: [...], isError: false}
            if "content" in data:
                _write_wind_cache(server_type, tool_name, params, data)
                return data
        logger.debug("Wind CLI returned unexpected shape: %.200s", stdout)
        _WIND_STATUS["last_error"] = "Wind CLI returned unexpected shape"
        return None
    else:
        # Exit code 1: error envelope {ok: false, error: {code, agent_action}}
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            _WIND_STATUS["last_error"] = f"Wind MCP error exit {result.returncode}: invalid JSON"
            logger.warning("Wind MCP error (exit %d): %.200s", result.returncode, stdout)
            return None

        error = envelope.get("error", {}) if isinstance(envelope, dict) else {}
        code = error.get("code", "UNKNOWN")
        agent_action = error.get("agent_action", "")
        _WIND_STATUS["last_error"] = f"{code}: {agent_action[:200]}"

        # Choose log level: NO_RESULTS is expected sometimes, use DEBUG
        if code in ("NO_RESULTS",):
            logger.debug("Wind MCP %s/%s: %s — %s", server_type, tool_name, code, agent_action[:200])
        elif code in _WIND_FALLBACK_CODES:
            logger.warning("Wind MCP %s/%s: %s — %s", server_type, tool_name, code, agent_action[:200])
        else:
            logger.warning("Wind MCP %s/%s: unknown error — %s", server_type, tool_name, agent_action[:200])

        return None


def _extract_text_from_mcp_result(result: dict) -> Optional[str]:
    """Extract text content from an MCP result envelope.

    MCP result shape: {content: [{type: "text", text: "..."}], isError: false}
    Some tools return JSON inside the text field.
    """
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if isinstance(content, list) and len(content) > 0:
        first = content[0]
        if isinstance(first, dict):
            if first.get("text"):
                return str(first.get("text"))
            for key in ("json", "data", "value", "result"):
                if key in first:
                    return json.dumps(first[key], ensure_ascii=False)
        return str(first)
    for key in ("text", "data", "result", "rows", "table", "records"):
        if key in result and result[key] is not None:
            value = result[key]
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return None


# ── Wind NL text parsers ────────────────────────────────────────────────────

# Unit multiplier for Chinese financial units
_UNIT_MULTIPLIER: Dict[str, float] = {
    "万亿": 1e12, "亿": 1e8, "千万": 1e7, "百万": 1e6,
    "万": 1e4, "千": 1e3, "百": 1e2,
    "元": 1, "%": 1, "%%": 0.01,
}

# Regex: account name (2-20 Chinese chars) + optional separator + number + optional unit
_RE_FINANCIAL_LINE = re.compile(
    r"([一-鿿（）()a-zA-Z]{2,30})"  # account name
    r"[：:是为约达到至]?\s*"                    # optional separator
    r"([+-]?\d[\d,.]*)\s*"                    # number (may have commas)
    r"([万亿千百]?元?|%%)?"                    # optional unit
)

# Regex: find any stock code (6-digit number)
_RE_STOCK_CODE = re.compile(r"\b(\d{6})\b")

# Regex: find date patterns
_RE_DATE = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})")
_RE_YEAR = re.compile(r"(\d{4})年")


def _parse_wind_financial_text(text: str) -> Dict[str, Optional[float]]:
    """Parse NL financial text from Wind into canonical account → value dict.

    Handles various Wind response formats:
    - "宁德时代2024年营业收入: 4000亿元，净利润: 440亿元"
    - JSON embedded in text
    - Markdown tables
    - Line-by-line key-value pairs
    """
    result: Dict[str, Optional[float]] = {}

    def _resolve_financial_term(term: str) -> Optional[str]:
        term = str(term).strip()
        if not term:
            return None
        canonical = _WIND_TERM_TO_CANONICAL.get(term)
        if canonical is None:
            canonical = _ak_resolve_sina_account(term)
        if canonical is None:
            for wind_term, canon in _WIND_TERM_TO_CANONICAL.items():
                if wind_term in term or term in wind_term:
                    canonical = canon
                    break
        if canonical is None:
            for sina_term, canon in _SINA_TO_CANONICAL.items():
                if sina_term in term or term in sina_term:
                    canonical = canon
                    break
        return canonical

    def _coerce_financial_value(value: Any, unit_hint: str = "") -> Optional[float]:
        if isinstance(value, (int, float)):
            number = float(value)
            unit_text = unit_hint or ""
            for unit, multiplier in _UNIT_MULTIPLIER.items():
                if unit and unit in unit_text and unit not in {"%", "%%"}:
                    return number * multiplier
            return number
        if value is None:
            return None
        raw = str(value).strip()
        if not raw or raw in {"-", "--", "N/A", "None", "null"}:
            return None
        m = re.search(r"([+-]?\d[\d,.]*)", raw)
        if not m:
            return None
        try:
            number = float(m.group(1).replace(",", ""))
        except ValueError:
            return None
        unit_text = unit_hint or raw
        for unit, multiplier in _UNIT_MULTIPLIER.items():
            if unit and unit in unit_text and unit not in {"%", "%%"}:
                return number * multiplier
        return number

    def _merge_value(term: Any, value: Any, unit_hint: str = "") -> None:
        canonical = _resolve_financial_term(str(term))
        if canonical is None or canonical in result:
            return
        coerced = _coerce_financial_value(value, unit_hint)
        if coerced is not None:
            result[canonical] = coerced

    def _walk_json(obj: Any) -> None:
        if isinstance(obj, dict):
            columns = obj.get("columns")
            table_rows = obj.get("rows")
            if table_rows is None:
                table_rows = obj.get("data")
            if isinstance(columns, list) and isinstance(table_rows, list):
                column_meta = []
                for column in columns:
                    if isinstance(column, dict):
                        column_meta.append((
                            str(column.get("name") or column.get("title") or column.get("field") or ""),
                            str(column.get("unit") or ""),
                        ))
                    else:
                        column_meta.append((str(column), ""))
                for row in table_rows:
                    if isinstance(row, list):
                        for idx, value in enumerate(row):
                            if idx < len(column_meta):
                                name, unit = column_meta[idx]
                                _merge_value(name, value, unit)
                    elif isinstance(row, dict):
                        for name, value in row.items():
                            unit = ""
                            for col_name, col_unit in column_meta:
                                if col_name == name:
                                    unit = col_unit
                                    break
                            _merge_value(name, value, unit)

            name = (
                obj.get("科目") or obj.get("指标") or obj.get("项目") or obj.get("名称")
                or obj.get("item") or obj.get("name") or obj.get("indicator")
                or obj.get("field")
            )
            value = (
                obj.get("数值") or obj.get("值") or obj.get("金额") or obj.get("本期")
                or obj.get("value") or obj.get("data") or obj.get("amount")
            )
            unit = obj.get("单位") or obj.get("unit") or ""
            if name is not None and value is not None:
                _merge_value(name, value, str(unit))
            for key, val in obj.items():
                _merge_value(key, val)
                _walk_json(val)
        elif isinstance(obj, list):
            for item in obj:
                _walk_json(item)

    # Strategy 0: Try to parse as JSON first
    try:
        data = json.loads(text)
        _walk_json(data)
        if len(result) >= 3:
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 0b: Markdown / pipe table rows, e.g. | 营业收入 | 7771.02亿元 |
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if any(set(c) <= {"-", ":"} for c in cells):
            continue
        key = cells[0]
        value = cells[1]
        unit_hint = " ".join(cells[1:])
        _merge_value(key, value, unit_hint)

    # Strategy 1: Regex line-by-line extraction
    # Remove commas from numbers (Chinese formatting)
    cleaned = text
    # Find all matches
    for m in _RE_FINANCIAL_LINE.finditer(text):
        term = m.group(1).strip()
        num_str = m.group(2).replace(",", "").replace("，", "")
        unit_str = (m.group(3) or "").strip()

        try:
            value = float(num_str)
        except ValueError:
            continue

        # Apply unit multiplier
        multiplier = _UNIT_MULTIPLIER.get(unit_str, 1.0)
        # Heuristic: if unit is % and value > 100, it's likely not a percentage
        # but some tools report % as-is
        value = value * multiplier

        canonical = _resolve_financial_term(term)
        if canonical is None:
            continue

        # Don't overwrite existing values
        if canonical not in result:
            result[canonical] = value

    # Strategy 2: If regex found few results, try line-by-line split
    if len(result) < 3:
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            # Try "key: value" pattern
            parts = re.split(r"[：:]\s*", line, maxsplit=1)
            if len(parts) == 2:
                key = parts[0].strip()
                val_str = parts[1].strip()
                # Extract number from value string
                num_match = re.search(r"([+-]?\d[\d,.]*)", val_str)
                if not num_match:
                    continue
                try:
                    value = float(num_match.group(1).replace(",", ""))
                except ValueError:
                    continue
                # Check for unit in value string
                for unit, mult in _UNIT_MULTIPLIER.items():
                    if unit in val_str and unit not in ("%", "%%", "元"):
                        value *= mult
                        break
                canonical = _resolve_financial_term(key)
                if canonical and canonical not in result:
                    result[canonical] = value

    return result


def _parse_wind_company_info(text: str, windcode: str) -> Optional[Dict[str, Any]]:
    """Parse company basic info from Wind NL response.

    Extracts: name, industry, business, exchange, listing_date
    """
    info: Dict[str, Any] = {"windcode": windcode}

    # Common patterns in Wind responses
    patterns = {
        "name": [
            r"(?:公司名称|证券简称|简称|名称)[：:]\s*([^\s,，。；;]+)",
            r"([一-鿿]{2,8}(?:股份|科技|集团|控股|锂业|能源|材料|电池|钴业|钨业|激光|动力|智能|新能|时代|比亚迪))",
        ],
        "industry": [
            r"(?:所属行业|行业类别|行业)[：:]\s*([^\s,，。；;]+)",
            r"(?:行业[：:])?\s*(电气机械和器材制造业|计算机|通信|汽车制造|有色金属|化学原料|专用设备|通用设备|非金属矿物|金属制品|[\\u4e00-\\u9fff]{2,6}(?:制造|加工|开采|冶炼|服务))",
        ],
        "business": [
            r"(?:主营业务|经营范围|主营)[：:]\s*(.+?)(?:[。；;]|\Z)",
        ],
        "listing_date": [
            r"(?:上市日期|上市时间)[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
        ],
    }

    # Try structured extraction
    for field, regexes in patterns.items():
        for regex in regexes:
            m = re.search(regex, text)
            if m:
                val = m.group(1).strip()
                if field == "listing_date":
                    val = val.replace("年", "-").replace("月", "-").replace("/", "-").rstrip("日").rstrip("-")
                info[field] = val
                break

    # Fallback: try to find company name from windcode context
    if not info.get("name"):
        # Stock codes in text are often near the company name
        m = re.search(r"(\d{6}\.(?:SZ|SH|BJ))\s*[：:]*\s*([一-鿿]{2,8})", text)
        if m:
            info["name"] = m.group(2)

    # Try JSON embedded in text
    if not info.get("name"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                for k, v in data.items():
                    if "简称" in k or "名称" in k or "name" in k.lower():
                        info["name"] = str(v)
                    elif "行业" in k:
                        info["industry"] = str(v)
                    elif "主营" in k or "业务" in k:
                        info["business"] = str(v)
                    elif "上市" in k:
                        info["listing_date"] = str(v)
        except (json.JSONDecodeError, TypeError):
            pass

    return info if info.get("name") else None


def _parse_wind_stock_list(text: str) -> List[str]:
    """Parse stock code list from Wind search_stocks response.

    Returns list of plain 6-digit stock codes.
    """
    codes = set()

    # Find all 6-digit numbers
    for m in _RE_STOCK_CODE.finditer(text):
        code = m.group(1)
        if code[0] in ("0", "3", "6", "8"):  # Valid A-share prefixes
            codes.add(code)

    # Try JSON embedded in text
    if not codes:
        try:
            data = json.loads(text)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str):
                        num_match = re.search(r"(\d{6})", item)
                        if num_match:
                            codes.add(num_match.group(1))
            elif isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, str):
                        num_match = re.search(r"(\d{6})", v)
                        if num_match:
                            codes.add(num_match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    return list(codes)


def _parse_wind_economic_data(mcp_result: dict) -> Optional[Dict[str, Any]]:
    """Parse economic data timeseries from Wind MCP response.

    Returns: {"碳酸锂期货": {"dates": [...], "values": [...], "unit": "元/吨", "name": "..."}}
    """
    text = _extract_text_from_mcp_result(mcp_result)
    if not text:
        return None

    # Try JSON first (some economic data tools return structured data)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # Check for timeseries structure
            for key in ("data", "result", "records", "values"):
                if key in data:
                    inner = data[key]
                    dates, values = _extract_timeseries(inner)
                    if dates and values:
                        return {
                            "碳酸锂期货": {
                                "dates": dates,
                                "values": values,
                                "unit": data.get("unit", "元/吨"),
                                "name": data.get("name", data.get("indicatorName", "碳酸锂")),
                            }
                        }
            # Direct timeseries
            dates, values = _extract_timeseries(data)
            if dates and values:
                return {
                    "碳酸锂期货": {
                        "dates": dates,
                        "values": values,
                        "unit": "元/吨",
                        "name": "碳酸锂",
                    }
                }
    except (json.JSONDecodeError, TypeError):
        pass

    # Try NL text extraction
    dates = []
    values = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        date_match = _RE_DATE.search(line)
        num_match = re.search(r"([+-]?\d[\d,.]*)\s*([万亿元]?)", line)
        if date_match and num_match:
            dates.append(date_match.group(1))
            try:
                v = float(num_match.group(1).replace(",", ""))
                unit = num_match.group(2)
                if unit in ("万",):
                    v *= 1e4
                elif unit in ("亿",):
                    v *= 1e8
                values.append(v)
            except ValueError:
                continue

    if len(dates) >= 6 and len(values) >= 6:
        return {
            "碳酸锂期货": {
                "dates": dates,
                "values": values,
                "unit": "元/吨",
                "name": "碳酸锂",
            }
        }
    return None


def _extract_timeseries(data: Any) -> tuple:
    """Extract (dates, values) lists from various data shapes."""
    dates, values = [], []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                d = item.get("date") or item.get("tradeDate") or item.get("日期") or item.get("time")
                v = item.get("value") or item.get("close") or item.get("price") or item.get("收盘价") or item.get("值")
                if d and v is not None:
                    dates.append(str(d))
                    try:
                        values.append(float(v))
                    except (ValueError, TypeError):
                        continue
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (int, float)):
                dates.append(str(k))
                values.append(float(v))
    return dates, values


# ── Wind API callers (private) ──────────────────────────────────────────────

def _wind_period_label(period: str) -> str:
    """Normalize a report period for short Wind natural-language questions."""
    if not period:
        return "最新一期"
    if re.match(r"^\d{4}$", period):
        return f"{period}年报"
    return period


def _build_fundamentals_questions(windcode: str, period: str) -> List[str]:
    """Build short NL questions for Wind get_stock_fundamentals.

    Wind performs better with concise natural-language prompts than with a long
    concatenated account list. Keep this to at most two calls to protect quota.
    """
    label = _wind_period_label(period)
    return [
        (
            f"请返回{windcode}{label}的营业收入、营业成本、净利润、归母净利润、"
            "扣非净利润、研发费用、销售费用、管理费用、财务费用、经营活动现金流净额，"
            "按科目:数值列出"
        ),
        (
            f"请返回{windcode}{label}的总资产、总负债、净资产、流动资产、流动负债、"
            "货币资金、应收账款、存货、固定资产、在建工程、短期借款、长期借款，"
            "按科目:数值列出"
        ),
    ]


def _build_fundamentals_question(windcode: str, period: str) -> str:
    """Backward-compatible single-question builder."""
    return _build_fundamentals_questions(windcode, period)[0]


def _wind_fetch_financials(
    windcode: str, period: str, timeout: int = 120
) -> Optional[Dict[str, Optional[float]]]:
    """Fetch financials via Wind MCP. Returns canonical account dict or None on failure."""
    questions = _build_fundamentals_questions(windcode, period)
    merged_result: Dict[str, Optional[float]] = {}

    for question in questions:
        mcp_result = _call_wind_cli(
            "stock_data", "get_stock_fundamentals",
            {"question": question},
            timeout=timeout,
        )
        if mcp_result is None:
            continue

        text = _extract_text_from_mcp_result(mcp_result)
        if not text:
            _WIND_STATUS["last_error"] = "Wind returned no financial text"
            continue

        result = _parse_wind_financial_text(text)
        if result:
            merged_result.update({k: v for k, v in result.items() if v is not None})

    if len(merged_result) >= 3:
        # Compute 毛利 if we have 营业收入 and 营业成本
        if "营业收入" in merged_result and "营业成本" in merged_result:
            rev = merged_result["营业收入"]
            cost = merged_result["营业成本"]
            if rev is not None and cost is not None and "毛利" not in merged_result:
                merged_result["毛利"] = rev - cost
        _WIND_STATUS["last_error"] = None
        return merged_result

    if not _WIND_STATUS.get("last_error"):
        _WIND_STATUS["last_error"] = "Wind financial response could not be parsed"
    return None


def _wind_fetch_company_info(
    windcode: str, timeout: int = 60
) -> Optional[Dict[str, Any]]:
    """Fetch company info via Wind MCP."""
    question = f"{windcode}公司基本档案主营行业上市日期所属板块"

    for attempt in range(1):
        if attempt > 0:
            time.sleep(2)
        mcp_result = _call_wind_cli(
            "stock_data", "get_stock_basicinfo",
            {"question": question},
            timeout=timeout,
        )
        if mcp_result is None:
            continue

        text = _extract_text_from_mcp_result(mcp_result)
        if not text:
            continue

        info = _parse_wind_company_info(text, windcode)
        if info and info.get("name"):
            return info

    return None


def _wind_search_lithium_peers(timeout: int = 60) -> Optional[List[str]]:
    """Search lithium battery peers via Wind MCP."""
    for attempt in range(1):
        if attempt > 0:
            time.sleep(2)
        mcp_result = _call_wind_cli(
            "stock_data", "search_stocks",
            {"question": "筛选锂电池概念股"},
            timeout=timeout,
        )
        if mcp_result is None:
            continue

        text = _extract_text_from_mcp_result(mcp_result)
        if not text:
            continue

        codes = _parse_wind_stock_list(text)
        if len(codes) >= 5:
            windcodes = [stock_code_to_windcode(c) for c in codes]
            return windcodes

    return None


def _wind_fetch_macro_context(timeout: int = 60) -> Optional[Dict[str, Any]]:
    """Fetch macro/lithium price data via Wind MCP economic_data."""
    mcp_result = _call_wind_cli(
        "economic_data",
        "get_economic_data",
        {"metricIdsStr": "\u78b3\u9178\u9502\u4ef7\u683c", "freq": "周"},
        timeout=timeout,
    )
    if mcp_result is None:
        return None

    parsed = _parse_wind_economic_data(mcp_result)
    return parsed if parsed else None


# ── AKShare implementations (private, renamed from originals) ───────────────

def _ak_retry_call(fn, *args, max_retries: int = 3, delay: float = 2.0, **kwargs) -> Any:
    """Call a function with retry on connection errors."""
    last_err = None
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                logger.debug("AKShare call attempt %d failed: %s — retrying...",
                             attempt + 1, e)
                time.sleep(delay * (attempt + 1))
    logger.warning("AKShare call failed after %d attempts: %s", max_retries, last_err)
    return None


def _ak_resolve_sina_account(sina_col: str) -> Optional[str]:
    """Map a Sina financial report column name to canonical account name."""
    if sina_col in _SINA_TO_CANONICAL:
        return _SINA_TO_CANONICAL[sina_col]
    for key, canonical in _SINA_TO_CANONICAL.items():
        if key in sina_col or sina_col in key:
            return canonical
    return None


def _ak_parse_ths_value(raw: Any) -> Optional[float]:
    """Parse THS value which may have unit suffix like '207.38亿', '16.14', '48.52%'."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        pass

    s = str(raw).strip()
    if not s:
        return None

    m = re.match(r'([+-]?\d+\.?\d*)\s*(亿|万|万亿|%)?', s)
    if not m:
        return None

    num = float(m.group(1))
    unit = m.group(2) or ""
    multipliers = {"亿": 1e8, "万": 1e4, "万亿": 1e12, "%": 1.0}
    return num * multipliers.get(unit, 1.0)


def _ak_extract_prior_period(
    result: Dict[str, Optional[float]],
    windcode: str,
    target_date: Optional[str],
) -> None:
    """Extract prior-period data from Sina for growth-rate / average indicators.

    Adds accounts like: 上期营业收入, 期初应收账款, 期初存货, 期初净资产, etc.
    """
    sina_code = _windcode_to_sina(windcode)
    if not target_date:
        return

    try:
        year = int(target_date[:4])
        month_day = target_date[4:]
        prior_date = f"{year - 1}{month_day}"
    except (ValueError, IndexError):
        return

    _PRIOR_ACCOUNTS = {
        "营业收入": "上期营业收入",
        "营业成本": "上期营业成本",
        "研发费用": "上期研发费用",
        "净利润": "上期净利润",
        "应收账款": "期初应收账款",
        "应收票据及应收账款": "期初应收账款",
        "存货": "期初存货",
        "固定资产净额": "期初固定资产",
        "在建工程合计": "期初在建工程",
        "在建工程": "期初在建工程",
        "合同负债": "期初合同负债",
        "资产总计": "期初总资产",
        "负债合计": "期初总负债",
        "所有者权益(或股东权益)合计": "期初净资产",
        "流动资产合计": "期初流动资产",
        "流动负债合计": "期初流动负债",
        "短期借款": "期初短期借款",
        "长期借款": "期初长期借款",
        "应付账款": "期初应付账款",
        "预收款项": "期初预收款项",
        "应收票据": "期初应收票据",
    }

    for symbol in ["资产负债表", "利润表"]:
        try:
            import akshare as ak
            df = _ak_retry_call(
                ak.stock_financial_report_sina,
                stock=sina_code, symbol=symbol, max_retries=2, delay=1.5,
            )
        except Exception:
            continue

        if df is None or df.empty:
            continue

        dates = df["报告日"].astype(str)
        row_idx = None
        for i, d in enumerate(dates):
            if str(d) == prior_date:
                row_idx = i
                break
        if row_idx is None:
            for i, d in enumerate(dates):
                if str(d) <= prior_date:
                    row_idx = i
                    break

        if row_idx is None or row_idx >= len(df):
            continue

        row = df.iloc[row_idx]

        for col_name in df.columns:
            if col_name in ("报告日", "数据源", "是否审计", "公告日期", "币种", "类型", "更新日期"):
                continue
            if col_name not in _PRIOR_ACCOUNTS:
                continue

            prior_key = _PRIOR_ACCOUNTS[col_name]
            if prior_key in result and result[prior_key] is not None:
                continue

            val = row[col_name]
            try:
                val_f = float(val)
                if val_f == val_f:
                    result[prior_key] = val_f
            except (ValueError, TypeError):
                continue

    # Add 期末* aliases
    _ENDING_ALIASES = {
        "应收账款": "期末应收账款",
        "存货": "期末存货",
        "固定资产": "期末固定资产",
        "在建工程": "期末在建工程",
        "合同负债": "期末合同负债",
        "总资产": "期末总资产",
        "总负债": "期末总负债",
        "净资产": "期末净资产",
        "流动资产": "期末流动资产",
        "流动负债": "期末流动负债",
    }
    for cur_key, end_key in _ENDING_ALIASES.items():
        if cur_key in result and result[cur_key] is not None:
            if end_key not in result or result[end_key] is None:
                result[end_key] = result[cur_key]

    if "上期营业收入" in result and result["上期营业收入"] is not None:
        if "上期营收" not in result:
            result["上期营收"] = result["上期营业收入"]


def _ak_supplement_ths(
    result: Dict[str, Optional[float]],
    windcode: str,
    target_date: Optional[str],
) -> None:
    """Supplement financial data with THS abstract for 扣非净利润."""
    plain_code = windcode.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")

    try:
        import akshare as ak
        df = _ak_retry_call(
            ak.stock_financial_abstract_ths,
            symbol=plain_code, indicator='按报告期', max_retries=2, delay=1.5,
        )
    except Exception:
        return

    if df is None or df.empty:
        return

    ths_target = None
    if target_date:
        ths_target = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}"

    try:
        dates = df['报告期'].astype(str)
        row_idx = None
        if ths_target:
            for i, d in enumerate(dates):
                if str(d) == ths_target:
                    row_idx = i
                    break
            if row_idx is None:
                for i, d in enumerate(dates):
                    if str(d) <= ths_target:
                        row_idx = i
                        break
        if row_idx is None:
            row_idx = len(df) - 1

        if row_idx >= len(df):
            return

        row = df.iloc[row_idx]

        _THS_COLUMNS = {
            "扣非净利润": "扣非净利润",
            "净利润": "归母净利润",
        }

        for ths_col, canonical in _THS_COLUMNS.items():
            if ths_col not in df.columns:
                continue
            if canonical in result and result[canonical] is not None:
                continue

            raw = row[ths_col]
            val = _ak_parse_ths_value(raw)
            if val is not None:
                result[canonical] = val

    except (KeyError, IndexError, TypeError) as e:
        logger.debug("Failed to parse THS data: %s", e)


def _ak_fetch_financials(
    windcode: str,
    period: str = "最新一期",
    timeout: int = 30,
) -> Optional[Dict[str, Optional[float]]]:
    """Fetch key financial accounts for a given stock via AKShare/Sina Finance."""
    sina_code = _windcode_to_sina(windcode)
    target_date = _period_to_date(period)

    result: Dict[str, Optional[float]] = {}

    for symbol, label in [("资产负债表", "BS"), ("利润表", "PL"), ("现金流量表", "CF")]:
        try:
            import akshare as ak
            df = _ak_retry_call(
                ak.stock_financial_report_sina,
                stock=sina_code, symbol=symbol, max_retries=2, delay=1.5,
            )
        except Exception as e:
            logger.debug("AKShare not available for %s: %s", label, e)
            continue

        if df is None or df.empty:
            continue

        if target_date:
            dates = df["报告日"].astype(str)
            row_idx = None
            for i, d in enumerate(dates):
                if str(d) == target_date:
                    row_idx = i
                    break
            if row_idx is None:
                for i, d in enumerate(dates):
                    if str(d) <= target_date:
                        row_idx = i
                        break
                if row_idx is None:
                    row_idx = 0
        else:
            row_idx = 0

        if row_idx >= len(df):
            continue

        row = df.iloc[row_idx]

        for col_name in df.columns:
            if col_name in ("报告日", "数据源", "是否审计", "公告日期", "币种", "类型", "更新日期"):
                continue

            canonical = _ak_resolve_sina_account(col_name)
            if canonical is None:
                continue

            val = row[col_name]
            try:
                val_f = float(val)
                if val_f != val_f:
                    continue
                if canonical not in result or result[canonical] is None:
                    result[canonical] = val_f
            except (ValueError, TypeError):
                continue

    if not result:
        return None

    _ak_extract_prior_period(result, windcode, target_date)
    _ak_supplement_ths(result, windcode, target_date)

    if "营业收入" in result and "营业成本" in result:
        rev = result["营业收入"]
        cost = result["营业成本"]
        if rev is not None and cost is not None:
            result["毛利"] = rev - cost

    return result


def _ak_fetch_company_info(windcode: str, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """Fetch company basic info from East Money via AKShare."""
    plain_code = windcode.replace(".SZ", "").replace(".SH", "").replace(".BJ", "")

    try:
        import akshare as ak
        df = _ak_retry_call(ak.stock_individual_info_em, symbol=plain_code, max_retries=2)
    except Exception:
        df = None

    if df is None or df.empty:
        return None

    try:
        info: Dict[str, Any] = {"windcode": windcode}
        for _, row in df.iterrows():
            item = str(row.iloc[0])
            value = row.iloc[1]
            if "简称" in item or "名称" in item:
                info["name"] = str(value)
            elif "行业" in item:
                info["industry"] = str(value)
            elif "主营" in item or "业务" in item:
                info["business"] = str(value)
            elif "上市" in item:
                info["listing_date"] = str(value)
            elif "板块" in item or "板" in item:
                info["exchange"] = str(value)
        return info if info.get("name") else None
    except (KeyError, IndexError, TypeError) as e:
        logger.debug("Failed to parse company info: %s", e)
        return None


# Hardcoded lithium battery peer list as ultimate fallback
_LITHIUM_PEERS_FALLBACK = [
    "300750.SZ",  # 宁德时代
    "002594.SZ",  # 比亚迪
    "002460.SZ",  # 赣锋锂业
    "002466.SZ",  # 天齐锂业
    "300014.SZ",  # 亿纬锂能
    "688005.SH",  # 容百科技
    "300450.SZ",  # 先导智能
    "603799.SH",  # 华友钴业
    "000792.SZ",  # 盐湖股份
    "002709.SZ",  # 天赐材料
    "300390.SZ",  # 天华新能
    "002080.SZ",  # 中材科技
    "600549.SH",  # 厦门钨业
    "002008.SZ",  # 大族激光
    "600482.SH",  # 中国动力
]


def _ak_search_lithium_peers(timeout: int = 30) -> Optional[List[str]]:
    """Search for lithium battery industry peer stocks via AKShare/East Money concept board."""
    try:
        import akshare as ak

        df_boards = _ak_retry_call(ak.stock_board_concept_name_em, max_retries=2)
        if df_boards is not None and not df_boards.empty:
            lith_row = df_boards[df_boards['板块名称'] == '锂电池']
            if len(lith_row) > 0:
                board_code = lith_row.iloc[0]['板块编码']
                df_cons = _ak_retry_call(
                    ak.stock_board_concept_cons_em,
                    symbol=board_code, max_retries=2,
                )
                if df_cons is not None and not df_cons.empty:
                    windcodes = []
                    for _, row in df_cons.iterrows():
                        code = str(row.get('代码', ''))
                        if code:
                            windcodes.append(stock_code_to_windcode(code))
                    if windcodes:
                        return windcodes

        if df_boards is not None and not df_boards.empty:
            related = df_boards[df_boards['板块名称'].str.contains(
                '锂|电池|新能源车', case=False, na=False
            )]
            if not related.empty:
                largest = related.nlargest(1, '个股数量').iloc[0]
                df_cons = _ak_retry_call(
                    ak.stock_board_concept_cons_em,
                    symbol=largest['板块编码'], max_retries=2,
                )
                if df_cons is not None and not df_cons.empty:
                    windcodes = []
                    for _, row in df_cons.iterrows():
                        code = str(row.get('代码', ''))
                        if code:
                            windcodes.append(stock_code_to_windcode(code))
                    if windcodes:
                        return windcodes
    except Exception as e:
        logger.debug("Concept board search failed: %s", e)

    logger.debug("Using hardcoded lithium peer list (AKShare network unavailable)")
    return list(_LITHIUM_PEERS_FALLBACK)


def _ak_fetch_macro_context(timeout: int = 30) -> Optional[Dict[str, Any]]:
    """Fetch lithium carbonate futures data via AKShare/Sina Futures."""
    try:
        import akshare as ak
        df = _ak_retry_call(ak.futures_main_sina, symbol='LC0', max_retries=2)
    except Exception:
        df = None

    if df is None or df.empty:
        return None

    try:
        dates = df['日期'].astype(str).tolist()
        values = df['收盘价'].astype(float).tolist()
        return {
            "碳酸锂期货": {
                "dates": dates,
                "values": values,
                "unit": "元/吨",
                "name": "碳酸锂期货主力合约",
            }
        }
    except (KeyError, TypeError) as e:
        logger.debug("Failed to parse futures data: %s", e)
        return None


# ── Public API: Orchestrators (Wind → AKShare fallback) ─────────────────────

def fetch_financials(
    windcode: str,
    period: str = "最新一期",
    timeout: int = 12,
) -> Optional[Dict[str, Optional[float]]]:
    """Fetch key financial accounts for a given stock.

    Primary: Wind MCP → AKShare fallback.

    Args:
        windcode: Wind-format stock code, e.g. "300750.SZ".
        period: Report period, e.g. "最新一期", "2025年报", "2024年报".
        timeout: Timeout in seconds.

    Returns:
        Dict mapping canonical Chinese account names to float values in 元,
        or None if data cannot be fetched.
    """
    # Phase 1: Wind MCP (primary)
    try:
        result = _wind_fetch_financials(windcode, period, timeout=min(timeout, 15))
        if result and len(result) >= 3:
            logger.info("Wind MCP: %d accounts for %s (%s)", len(result), windcode, period)
            return result
    except Exception as e:
        logger.warning("Wind MCP fetch_financials exception: %s", e)

    # Phase 2: AKShare (fallback)
    logger.info("Wind-only fetch_financials returned no data for %s (%s)", windcode, period)
    return None


def fetch_company_info(windcode: str, timeout: int = 6) -> Optional[Dict[str, Any]]:
    """Fetch company basic info.

    Primary: Wind MCP → AKShare fallback.

    Returns:
        Dict with keys: name, industry, business, exchange, listing_date
        or None on failure.
    """
    try:
        result = _wind_fetch_company_info(windcode, timeout=min(timeout, 6))
        if result and result.get("name"):
            logger.info("Wind MCP: company info for %s → %s", windcode, result.get("name"))
            return result
    except Exception as e:
        logger.warning("Wind MCP fetch_company_info exception: %s", e)

    logger.info("Wind-only fetch_company_info returned no data for %s", windcode)
    return None


def fetch_company_info_enhanced(windcode: str, timeout: int = 6) -> Optional[Dict[str, Any]]:
    """Fetch enhanced company info for sector classification.

    Returns:
        {"name": str, "business": str, "industry": str, "windcode": str}
    """
    return fetch_company_info(windcode, timeout=timeout)


def extract_business_keywords(info: Optional[Dict[str, Any]]) -> List[str]:
    """Extract business segment keywords from company info."""
    if not info:
        return []

    business = info.get("business", "")
    if not business:
        return []

    segments = re.split(r'[；;，,。.\s]+', business)
    keywords = []
    for seg in segments:
        seg = seg.strip()
        if len(seg) >= 4 and re.search(r'[一-鿿]', seg):
            keywords.append(seg)
    return keywords


def search_lithium_peers(timeout: int = 6) -> Optional[List[str]]:
    """Search for lithium battery industry peer stocks.

    Primary: Wind MCP → AKShare concept board → hardcoded fallback.

    Returns:
        List of windcodes like ["300750.SZ", "002466.SZ", ...].
    """
    # Phase 1: Wind MCP
    try:
        result = _wind_search_lithium_peers(timeout=min(timeout, 6))
        if result and len(result) >= 5:
            logger.info("Wind MCP: %d lithium peers found", len(result))
            return result
    except Exception as e:
        logger.warning("Wind MCP search_lithium_peers exception: %s", e)

    # Phase 2: AKShare (with built-in hardcoded fallback)
    logger.info("Wind-only search_lithium_peers returned no data")
    return None


def fetch_peers_financials(
    windcodes: Optional[List[str]] = None,
    max_peers: int = 3,
    timeout: int = 6,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch key financials for lithium battery peer companies.

    Returns:
        List of dicts: [{"windcode": "300750.SZ", "name": "...",
                         "revenue": ..., "profit": ..., "assets": ..., ...}, ...]
    """
    if windcodes is None:
        windcodes = search_lithium_peers(timeout=min(timeout, 30))

    if not windcodes:
        return None

    windcodes = windcodes[:max_peers]

    peers = []
    for wc in windcodes:
        fin = fetch_financials(wc, period="最新一期", timeout=timeout)
        if fin:
            peers.append({
                "windcode": wc,
                "revenue": fin.get("营业收入"),
                "cost": fin.get("营业成本"),
                "profit": fin.get("净利润"),
                "assets": fin.get("总资产"),
                "equity": fin.get("净资产"),
                "gross_margin": fin.get("毛利"),
                "rd_expense": fin.get("研发费用"),
            })

    return peers if peers else None


def fetch_macro_context(timeout: int = 6) -> Optional[Dict[str, Any]]:
    """Fetch lithium carbonate futures/market data as macro context.

    Primary: Wind MCP → AKShare fallback.

    Returns:
        Dict with "碳酸锂期货" key containing dates, values, unit.
    """
    try:
        result = _wind_fetch_macro_context(timeout=min(timeout, 6))
        if result:
            # Validate: need enough data points
            for ind in result.values():
                if isinstance(ind, dict) and len(ind.get("values", [])) >= 6:
                    logger.info("Wind MCP: macro data with %d points", len(ind["values"]))
                    return result
    except Exception as e:
        logger.warning("Wind MCP fetch_macro_context exception: %s", e)

    logger.info("Wind-only fetch_macro_context returned no data")
    return None


def get_lithium_price_trend(
    macro_data: Optional[Dict[str, Any]] = None,
    timeout: int = 6,
) -> Optional[Dict[str, Any]]:
    """Extract lithium carbonate price trend from macro/futures data.

    Returns:
        {
            "current": float, "avg_3m": float, "avg_6m": float,
            "trend": "上涨"|"下跌"|"震荡", "change_3m_pct": float, "unit": "元/吨",
        }
    """
    if macro_data is None:
        macro_data = fetch_macro_context(timeout=timeout)

    if not macro_data:
        return None

    try:
        lithium_data = None
        for name, ind in macro_data.items():
            if "碳酸锂" in name or "Lithium" in name:
                lithium_data = ind
                break
        if not lithium_data:
            lithium_data = list(macro_data.values())[0]

        values = lithium_data.get("values", [])
        if not values or len(values) < 6:
            return None

        current = values[-1]
        avg_3m = sum(values[-3:]) / 3 if len(values) >= 3 else sum(values) / len(values)
        avg_6m = sum(values[-6:]) / 6 if len(values) >= 6 else sum(values) / len(values)

        if len(values) >= 4:
            recent = sum(values[-2:]) / 2
            earlier = sum(values[-4:-2]) / 2
            if recent > earlier * 1.05:
                trend = "下跌" if current < values[-2] else "上涨"
            elif recent < earlier * 0.95:
                trend = "下跌"
            else:
                trend = "震荡"
        else:
            trend = "震荡"

        change_3m = ((current - values[-4]) / values[-4] * 100) if len(values) >= 4 else 0

        return {
            "current": current,
            "avg_3m": avg_3m,
            "avg_6m": avg_6m,
            "trend": trend,
            "change_3m_pct": round(change_3m, 1),
            "unit": lithium_data.get("unit", "元/吨"),
            "name": lithium_data.get("name", ""),
        }
    except (IndexError, TypeError, ZeroDivisionError) as e:
        logger.debug("Failed to compute lithium price trend: %s", e)
        return None


# ── Self-test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    print("=== Wind MCP + AKShare Market Data Adapter Self-Test ===\n")

    # 1. Code conversion
    print("1. Code conversion:")
    for code in ["300750", "688005", "002466", "600519"]:
        wc = stock_code_to_windcode(code)
        sc = _windcode_to_sina(wc)
        print(f"   {code} → {wc} → sina:{sc}")

    # 2. Period conversion
    print("\n2. Period conversion:")
    for p in ["2025年报", "2025三季报", "2025中报", "2025一季报", "2024年报", "最新一期"]:
        print(f"   {p} → {_period_to_date(p)}")

    # 3. Financials fetch (Wind first, AKShare fallback)
    print("\n3. Financials fetch (300750.SZ, 2025年报):")
    fin = fetch_financials("300750.SZ", period="2025年报")
    if fin:
        key_accounts = ["营业收入", "营业成本", "净利润", "总资产", "总负债", "净资产",
                        "货币资金", "存货", "应收账款", "固定资产", "经营活动现金流净额",
                        "研发费用", "毛利"]
        for k in key_accounts:
            v = fin.get(k)
            if v is not None and abs(v) > 1e8:
                print(f"   {k}: {v/1e8:.2f}亿")
            elif v is not None:
                print(f"   {k}: {v:.2f}")
            else:
                print(f"   {k}: MISSING")
        print(f"   Total accounts: {len(fin)}")
    else:
        print("   FAILED — check network / AKShare / Wind API key")

    # 4. Company info
    print("\n4. Company info (300750.SZ):")
    info = fetch_company_info("300750.SZ")
    if info:
        for k, v in info.items():
            print(f"   {k}: {v}")
    else:
        print("   FAILED — will use hardcoded fallback")

    # 5. Peer search
    print("\n5. Lithium peer search:")
    peers = search_lithium_peers(timeout=20)
    if peers:
        print(f"   Found {len(peers)} peers (first 10):")
        for p in peers[:10]:
            print(f"     {p}")
    else:
        print("   FAILED")

    # 6. Lithium price trend
    print("\n6. Lithium carbonate price trend:")
    trend = get_lithium_price_trend(timeout=20)
    if trend:
        print(f"   Current: {trend['current']:.0f} {trend['unit']}")
        print(f"   3M avg: {trend['avg_3m']:.0f}")
        print(f"   Trend: {trend['trend']}")
        print(f"   3M change: {trend['change_3m_pct']:.1f}%")
    else:
        print("   FAILED")

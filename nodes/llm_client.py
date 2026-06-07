"""
Shared LLM client for FinAgent-Lithium nodes that require LLM calls.

Uses DeepSeek's Anthropic-compatible Messages API.
Configure via environment variables or direct parameters.
"""

import json
import os
import re
from typing import Dict, List, Optional, Any


# ── Configuration ─────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
    "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic"),
    "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    "max_tokens": 2048,
    "temperature": 0.3,
}


# ── LLM Call ──────────────────────────────────────────────────────────

def call_llm(
    system_prompt: str,
    user_message: str,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Call the LLM with a system prompt and user message.

    Args:
        system_prompt: System-level instructions.
        user_message: The user message (data to analyze).
        config: Override default config. Must include 'api_key'.

    Returns:
        LLM response text, or None if the call fails.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    if not cfg["api_key"]:
        return _mock_response(system_prompt, user_message)

    try:
        import requests
    except ImportError:
        return _mock_response(system_prompt, user_message)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": cfg["model"],
        "max_tokens": cfg["max_tokens"],
        "temperature": cfg["temperature"],
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_message}
        ],
    }

    url = f"{cfg['base_url'].rstrip('/')}/v1/messages"

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        # Anthropic Messages format: content[0].text
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block["text"]
        return None
    except Exception as e:
        print(f"[LLM] API call failed: {e}")
        return None


def _mock_response(system_prompt: str, user_message: str) -> Optional[str]:
    """Return a mock response for testing when no API key is available."""
    # Parse the input to understand what indicators are provided
    context = ""
    try:
        data = json.loads(user_message) if isinstance(user_message, str) else user_message
        sector = data.get("sector_level2", "unknown")
        key_inds = data.get("key_indicators", {})
        general_inds = data.get("general_indicators", {})

        # Build a context-aware mock response
        all_inds = {**key_inds, **general_inds}
        high_risk_items = []
        warning_items = []
        for name, ind in all_inds.items():
            risk = str(ind.get("risk_level", ""))
            val = ind.get("value")
            if "高风险" in risk and val is not None:
                high_risk_items.append((name, val))
            elif "警惕" in risk and val is not None:
                warning_items.append((name, val))

        mock_results = []

        if high_risk_items:
            names = ", ".join(f"{n}={v}" for n, v in high_risk_items[:3])
            mock_results.append({
                "combination": " + ".join(n for n, _ in high_risk_items[:3]),
                "status": "高危状态",
                "description": f"检测到多个高风险指标：{names}。这组数据说明企业经营面临较大压力，核心指标显著偏离健康区间。",
                "investment_implication": "建议密切关注后续季报变化，评估风险是否持续恶化。",
                "evidence": {n: v for n, v in high_risk_items[:3]},
            })

        if warning_items:
            names = ", ".join(f"{n}={v}" for n, v in warning_items[:2])
            mock_results.append({
                "combination": " + ".join(n for n, _ in warning_items[:2]),
                "status": "风险累积",
                "description": f"警惕指标：{names}。这些指标处于关注区间，虽然尚未到危险程度，但趋势值得跟踪。",
                "investment_implication": "关注这些指标的后续走势，若持续恶化需及时调整判断。",
                "evidence": {n: v for n, v in warning_items[:2]},
            })

        if not mock_results:
            mock_results.append({
                "combination": "指标整体平稳",
                "status": "最优状态",
                "description": "当前各项指标均在正常范围内，未发现明显的联动风险模式。",
                "investment_implication": "经营状态健康，可维持现有投资判断。",
                "evidence": {},
            })

        context = json.dumps(mock_results[:5], ensure_ascii=False, indent=2)
    except Exception:
        context = json.dumps([{
            "combination": "数据不足",
            "status": "无法判定",
            "description": "提供的指标数据不足以进行联动诊断。",
            "investment_implication": "需要补充更多指标数据。",
            "evidence": {},
        }], ensure_ascii=False)

    return context


def extract_json_from_response(text: str) -> Optional[List[Dict]]:
    """Extract a JSON array from an LLM response that may contain markdown fences."""
    if not text:
        return None

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting from ```json ... ``` block
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Try extracting any JSON array
    m = re.search(r'\[[\s\S]*\]', text)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return None

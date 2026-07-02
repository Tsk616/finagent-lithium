"""
Shared LLM client for FinAgent-Lithium nodes that require LLM calls.

Uses DeepSeek's OpenAI-compatible Chat Completions API by default, with
Anthropic-compatible Messages API support kept for deployments that opt in.
Configure via environment variables or direct parameters.
"""

import json
import os
import re
from typing import Dict, List, Optional, Any


# ── Configuration ─────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
    "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    "max_tokens": 2048,
    "temperature": 0.3,
    "timeout": int(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "12")),
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

    url, headers, payload, is_anthropic = _build_request(cfg, system_prompt, user_message)

    # One retry with backoff: transient network drops / 429 / 5xx are common
    # enough on the free tier that a single retry meaningfully cuts silent
    # degradation. 4xx (except 429) are permanent -- don't retry those.
    last_err = None
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("timeout", 12))
            resp.raise_for_status()
            data = resp.json()
            if is_anthropic:
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        return block["text"]
            else:
                choices = data.get("choices") or []
                if choices:
                    message = choices[0].get("message") or {}
                    content = message.get("content")
                    if content:
                        return content
            return None
        except Exception as e:
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            retryable = status is None or status == 429 or status >= 500
            if attempt == 0 and retryable:
                import time
                time.sleep(1.5)
                continue
            print(f"[LLM] API call failed: {last_err}; status={status}; url={url}")
            return None
    return None


def _build_request(cfg: Dict[str, Any], system_prompt: str, user_message: str):
    """Build (url, headers, payload) for either API dialect."""
    base_url = str(cfg.get("base_url", "")).rstrip("/")
    is_anthropic = "/anthropic" in base_url
    headers = {"Content-Type": "application/json"}
    if is_anthropic:
        headers["anthropic-version"] = "2023-06-01"
        headers["x-api-key"] = cfg["api_key"]
        payload = {
            "model": cfg["model"],
            "max_tokens": cfg["max_tokens"],
            "temperature": cfg["temperature"],
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        url = f"{base_url}/v1/messages"
    else:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
        payload = {
            "model": cfg["model"],
            "max_tokens": cfg["max_tokens"],
            "temperature": cfg["temperature"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        url = f"{base_url}/chat/completions"
    return url, headers, payload, is_anthropic


def call_llm_stream(
    system_prompt: str,
    user_message: str,
    config: Optional[Dict[str, Any]] = None,
):
    """Stream the LLM response as text chunks (generator).

    Handles both SSE dialects: OpenAI Chat Completions (default DeepSeek
    endpoint) and Anthropic Messages (base_url containing '/anthropic').
    Yields nothing at all on failure so the caller can fall back to the
    non-streaming path. No API key -> yields the mock response once.
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}

    if not cfg["api_key"]:
        mock = _mock_response(system_prompt, user_message)
        if mock:
            yield mock
        return

    try:
        import requests
    except ImportError:
        return

    url, headers, payload, is_anthropic = _build_request(cfg, system_prompt, user_message)
    payload["stream"] = True

    try:
        # Tuple timeout: 10s connect, read timeout applies BETWEEN chunks
        resp = requests.post(url, headers=headers, json=payload, stream=True,
                             timeout=(10, cfg.get("timeout", 60)))
        resp.raise_for_status()
        for raw_line in resp.iter_lines(decode_unicode=True):
            if not raw_line or not raw_line.startswith("data:"):
                continue
            data_str = raw_line[5:].strip()
            if not data_str:
                continue
            if data_str == "[DONE]":
                return
            try:
                evt = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if is_anthropic:
                if evt.get("type") == "content_block_delta":
                    delta = evt.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif evt.get("type") == "message_stop":
                    return
            else:
                choices = evt.get("choices") or []
                if choices:
                    content = (choices[0].get("delta") or {}).get("content")
                    if content:
                        yield content
    except Exception as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        print(f"[LLM] stream call failed: {e}; status={status}; url={url}")
        return


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

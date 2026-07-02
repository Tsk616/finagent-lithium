"""
Follow-up Q&A routes for FinAgent-Lithium.

Handles the /api/ask endpoint which lets users ask questions about a
previously generated report, and /api/ask/stream which streams the answer
as Server-Sent Events. Uses the LLM with report context, falling back to
a deterministic answer when the LLM is unavailable.

Does NOT re-run the analysis pipeline -- it reads from stored state.
"""

import json
import os
import threading

from flask import Blueprint, request, jsonify, Response, stream_with_context

from nodes.llm_client import call_llm, call_llm_stream
from web.shared_state import REPORT_STATES

bp = Blueprint("followup", __name__)

MAX_QUESTION_CHARS = 1000
MAX_HISTORY_MESSAGES = 6   # last 3 rounds
MAX_HISTORY_ITEM_CHARS = 2000

# Each open SSE stream pins one gthread worker thread for its whole duration;
# with only a handful of threads, streams must not starve the status polling.
_STREAM_SLOTS = threading.BoundedSemaphore(
    max(int(os.environ.get("ASK_STREAM_MAX", "2")), 1)
)


def _validate_ask_payload(payload: dict):
    """Return (state, question, conversation) or (None, error_response, http_status)."""
    report_id = payload.get("report_id", "")
    question = str(payload.get("question", "")).strip()
    if not question:
        return None, {"status": "error", "message": "question is required"}, 400
    if len(question) > MAX_QUESTION_CHARS:
        return None, {"status": "error", "message": f"问题过长（上限 {MAX_QUESTION_CHARS} 字）"}, 400

    conversation = payload.get("conversation", [])
    if not isinstance(conversation, list):
        conversation = []
    cleaned = []
    for m in conversation[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content", ""))[:MAX_HISTORY_ITEM_CHARS]
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})

    state = REPORT_STATES.get(report_id)
    if not state:
        return None, {"status": "error", "message": "report_id not found or expired"}, 404
    return state, question, cleaned


@bp.route("/api/ask", methods=["POST"])
def api_ask():
    """Answer a follow-up question based on a generated report."""
    payload = request.get_json() or {}
    state, question_or_err, conversation_or_code = _validate_ask_payload(payload)
    if state is None:
        return jsonify(question_or_err), conversation_or_code
    question, conversation = question_or_err, conversation_or_code

    try:
        answer = _answer_followup_question(state, question, conversation)
    except Exception as e:
        print(f"[followup] Error answering question: {e}")
        answer = "抱歉，生成回答时出现错误，请稍后重试。"

    return jsonify({"status": "ok", "answer": answer})


@bp.route("/api/ask/stream", methods=["POST"])
def api_ask_stream():
    """Stream the follow-up answer as SSE frames: {"delta": ...} then {"done": true}.

    Returns 503 (busy) when all stream slots are taken -- the frontend then
    falls back to the non-streaming /api/ask.
    """
    payload = request.get_json() or {}
    state, question_or_err, conversation_or_code = _validate_ask_payload(payload)
    if state is None:
        return jsonify(question_or_err), conversation_or_code
    question, conversation = question_or_err, conversation_or_code

    if not _STREAM_SLOTS.acquire(blocking=False):
        return jsonify({"status": "busy", "fallback": True}), 503

    compact_state = _compact_report_state(state)
    system_prompt, user_message = _build_followup_prompt(compact_state, question, conversation)

    def generate():
        try:
            got_any = False
            for chunk in call_llm_stream(system_prompt, user_message, config={"timeout": 60}):
                got_any = True
                yield "data: " + json.dumps({"delta": chunk}, ensure_ascii=False) + "\n\n"
            if not got_any:
                fallback = _fallback_followup_answer(compact_state, question)
                yield "data: " + json.dumps({"delta": fallback}, ensure_ascii=False) + "\n\n"
            yield 'data: {"done": true}\n\n'
        finally:
            # Covers normal end AND client disconnect (GeneratorExit)
            _STREAM_SLOTS.release()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _compact_report_state(state: dict) -> dict:
    """Trim the full AnalysisState down to Q&A-relevant evidence."""
    return {
        "company_name": state.get("company_name"),
        "stock_code": state.get("stock_code"),
        "period": state.get("current_period"),
        "sector": state.get("sector_level2"),
        "data_completeness": state.get("data_completeness"),
        "weighted_score": state.get("weighted_score"),
        "risk_summary": state.get("risk_summary"),
        "metric_interpretations": (state.get("metric_interpretations") or [])[:12],
        "industry_benchmark_insights": state.get("industry_benchmark_insights"),
        "macro_insights": state.get("macro_insights"),
        "anomaly_signals": state.get("anomaly_signals") or [],
    }


def _build_followup_prompt(compact_state: dict, question: str, conversation: list = None):
    """Build (system_prompt, user_message) shared by both ask endpoints."""
    system_prompt = (
        "你是 FinAgent 财务分析助手。请用中文回答。"
        "只使用提供的报告数据回答问题。如果数据不足，明确说明。"
        "不要编造数据、同行对比或投资建议。回答要简洁专业，像分析师面对投资人。"
    )
    context_parts = [json.dumps({"report_data": compact_state}, ensure_ascii=False, default=str)]
    if conversation:
        history_text = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content', '')}"
            for m in conversation[-MAX_HISTORY_MESSAGES:]
        )
        context_parts.append(f"\n对话历史:\n{history_text}")
    context_parts.append(f"\n当前问题: {question}")
    return system_prompt, "\n".join(context_parts)


def _answer_followup_question(state: dict, question: str, conversation: list = None) -> str:
    """Use report evidence for follow-up Q&A, with a conservative fallback."""
    compact_state = _compact_report_state(state)
    system_prompt, user_message = _build_followup_prompt(compact_state, question, conversation)
    answer = call_llm(system_prompt, user_message, config={"timeout": 25})
    if answer:
        return answer.strip()
    return _fallback_followup_answer(compact_state, question)


def _fallback_followup_answer(compact_state: dict, question: str) -> str:
    """Deterministic fallback when the LLM is unavailable."""
    score = (compact_state.get("weighted_score") or {}).get("score")
    risks = compact_state.get("risk_summary") or {}
    anomalies = compact_state.get("anomaly_signals") or []
    lines = [
        f"基于当前报告回答：{compact_state.get('company_name') or '该公司'}"
        f"（{compact_state.get('period') or '报告期未注明'}）。"
    ]
    if score is not None:
        lines.append(f"综合评分为 {score}/100。")
    if risks.get("summary"):
        lines.append(str(risks["summary"]))
    if anomalies:
        lines.append(f"报告中检测到 {len(anomalies)} 条异常信号，应优先查看异常警报章节。")

    matched = []
    q = question.lower()
    for item in compact_state.get("metric_interpretations") or []:
        metric = str(item.get("metric", ""))
        if metric and (metric.lower() in q or any(token in q for token in metric.lower().split())):
            matched.append(item)
    for item in matched[:3]:
        lines.append(f"{item.get('metric')}：{item.get('interpretation')}")
    if not matched:
        lines.append("当前离线回答只能基于已生成的评分、异常和指标解读摘要；更细的归因需要 LLM 服务可用。")
    return "\n".join(lines)

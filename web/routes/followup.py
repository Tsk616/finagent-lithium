"""
Follow-up Q&A routes for FinAgent-Lithium.

Handles the /api/ask endpoint which lets users ask questions about a
previously generated report. Uses the LLM with report context, falling
back to a deterministic answer when the LLM is unavailable.

Does NOT re-run the analysis pipeline -- it reads from stored state.
"""

import json

from flask import Blueprint, request, jsonify

from nodes.llm_client import call_llm
from web.shared_state import REPORT_STATES

bp = Blueprint("followup", __name__)


@bp.route("/api/ask", methods=["POST"])
def api_ask():
    """Answer a follow-up question based on a generated report."""
    payload = request.get_json() or {}
    report_id = payload.get("report_id", "")
    question = str(payload.get("question", "")).strip()
    conversation = payload.get("conversation", [])
    if not question:
        return jsonify({"status": "error", "message": "question is required"}), 400

    state = REPORT_STATES.get(report_id)
    if not state:
        return jsonify({"status": "error", "message": "report_id not found or expired"}), 404

    try:
        answer = _answer_followup_question(state, question, conversation)
    except Exception as e:
        print(f"[followup] Error answering question: {e}")
        answer = "抱歉，生成回答时出现错误，请稍后重试。"

    return jsonify({"status": "ok", "answer": answer})


def _answer_followup_question(state: dict, question: str, conversation: list = None) -> str:
    """Use report evidence for follow-up Q&A, with a conservative fallback."""
    compact_state = {
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
    system_prompt = (
        "你是 FinAgent 财务分析助手。请用中文回答。"
        "只使用提供的报告数据回答问题。如果数据不足，明确说明。"
        "不要编造数据、同行对比或投资建议。回答要简洁专业，像分析师面对投资人。"
    )

    # Build context with conversation history
    context_parts = [json.dumps({"report_data": compact_state}, ensure_ascii=False, default=str)]
    if conversation:
        history_text = "\n".join(
            f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content', '')}"
            for m in conversation[-6:]  # Keep last 3 rounds
        )
        context_parts.append(f"\n对话历史:\n{history_text}")
    context_parts.append(f"\n当前问题: {question}")

    user_message = "\n".join(context_parts)
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

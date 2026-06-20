from __future__ import annotations

import logging
from typing import Any

from app.clients.vllm import chat_completion, parse_json_block
from app.config import settings

logger = logging.getLogger(__name__)

SOLVE_SYSTEM = """你是评测求解智能体（Learning Buddy）。禁止面向学生讲解。
在给出的课内 Method/Shortcut/学段约束内求解，禁止超纲（不可用大学工具）。
只输出 JSON：
- reference_answer: 标准答案
- solution_steps: string[] 解题步骤
- method_id: 使用的标准解法 id 或 null
- shortcuts_used: string[] 使用的二次结论 id
- solver_confidence: 0-1
- kp_id: 最匹配考纲 kp_id"""

JUDGE_SYSTEM = """你是评测判题智能体（Learning Buddy）。禁止讲解。
比较学生手写与参考解，判断数学/语义等价（非字面相等）。
只输出 JSON：
- is_wrong: true/false/null（无法判断 null）
- eval_confidence: 0-1
- error_type: concept|careless|incomplete|unreadable|null
- match_notes: 简短内部说明
- kp_id: 知识点 id"""


def solve_reference(
    *,
    print_text: str,
    eval_context: dict[str, Any],
    subject: str,
    grade_band: str,
    reference_answer: str | None = None,
) -> dict | None:
    if not settings.eval_agent_enabled:
        return None
    user = (
        f"subject={subject} grade={grade_band}\n"
        f"题干: {print_text}\n"
        f"课内上下文: {eval_context}\n"
        f"题库参考答(若有): {reference_answer or '(无)'}\n"
        "输出 JSON。"
    )
    try:
        raw = chat_completion(SOLVE_SYSTEM, user, max_tokens=768)
        data = parse_json_block(raw)
        if reference_answer and not data.get("reference_answer"):
            data["reference_answer"] = reference_answer
            data["reference_source"] = "rag_bank"
        else:
            data.setdefault("reference_source", "agent_solved")
        return data
    except Exception as e:
        logger.warning("solve_reference failed: %s", e)
        if reference_answer:
            return {
                "reference_answer": reference_answer,
                "reference_source": "rag_bank",
                "solver_confidence": 0.75,
                "solution_steps": [],
            }
        return None


def judge_against_reference(
    *,
    print_text: str,
    handwriting: str,
    reference: dict[str, Any],
    subject: str,
    grade_band: str,
) -> dict | None:
    if not settings.eval_agent_enabled:
        return None
    user = (
        f"subject={subject} grade={grade_band}\n"
        f"题干: {print_text}\n"
        f"参考解: {reference.get('reference_answer')}\n"
        f"步骤: {reference.get('solution_steps')}\n"
        f"学生手写: {handwriting or '(无)'}\n"
        "输出 JSON。"
    )
    try:
        raw = chat_completion(JUDGE_SYSTEM, user, max_tokens=512)
        return parse_json_block(raw)
    except Exception as e:
        logger.warning("judge_against_reference failed: %s", e)
        return None


def evaluate_question_full(
    *,
    print_text: str,
    handwriting: str,
    eval_context: dict[str, Any],
    subject: str,
    grade_band: str,
    reference_answer: str | None = None,
) -> dict | None:
    ref = solve_reference(
        print_text=print_text,
        eval_context=eval_context,
        subject=subject,
        grade_band=grade_band,
        reference_answer=reference_answer,
    )
    if not ref or not ref.get("reference_answer"):
        if settings.eval_fallback_rules and not (handwriting or "").strip():
            return {"is_wrong": None, "eval_confidence": 0.0, "error_type": "unreadable"}
        return None

    judge = judge_against_reference(
        print_text=print_text,
        handwriting=handwriting,
        reference=ref,
        subject=subject,
        grade_band=grade_band,
    )
    if not judge:
        return None

    solver_c = float(ref.get("solver_confidence", 0.6))
    compare_c = float(judge.get("eval_confidence", 0.6))
    rag_boost = 1.0 if ref.get("reference_source") == "rag_bank" else 0.9
    eval_confidence = min(solver_c, compare_c) * rag_boost

    return {
        **judge,
        **{k: v for k, v in ref.items() if k not in judge},
        "eval_confidence": round(eval_confidence, 4),
        "reference_source": ref.get("reference_source"),
        "kp_confidence": compare_c,
    }

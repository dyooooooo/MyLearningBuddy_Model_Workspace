from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.agents.eval_bank import answers_too_similar, normalize_answer
from app.agents.eval_trace import EvalTraceBuilder
from app.clients.vllm import (
    ChatCompletionResult,
    chat_completion,
    chat_completion_detail,
    parse_json_block_robust,
    salvage_json_fields,
)
from app.config import settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "eval_v4_hints"

_BANK_STEM_TRUST = 0.75

_SOLVE_SALVAGE = (
    "reference_answer",
    "solution_steps",
    "method_id",
    "solver_confidence",
    "kp_id",
)
_JUDGE_SALVAGE = (
    "is_wrong",
    "eval_confidence",
    "error_type",
    "match_notes",
    "reasoning_steps",
    "kp_id",
)

SOLVE_THINK_SYSTEM = """你是高中数学求解智能体（Learning Buddy C1 · SOLVE-THINK）。
任务：仅依据题干与下方课内知识点/解法/公式，独立逐步推导标准答案。
硬性规则：
1. 你看不到学生手写。
2. 若题干给出全集 U，补集答案只能包含 U 内元素；例如 U=(-∞,1) 时答案不得含 1 或 [1,+∞)。
3. 不得抄写课内材料中的最终数值答案。
请用自然语言输出：先逐步推理，最后一行以「最终答案：」给出结论。"""

SOLVE_FORMAT_SYSTEM = """你是结构化输出智能体（SOLVE-FORMAT）。
将下面的求解推导压缩为 JSON（勿用反斜杠 LaTeX，用 Unicode）：
- reference_answer: string 标准答案（简洁、可比对）
- solution_steps: string[] 最多 4 条，每条 ≤80 字，最后一步与 reference_answer 一致
- method_id: string|null
- shortcuts_used: string[]
- solver_confidence: number 0-1
- kp_id: string|null
只输出 JSON。"""

SOLVE_SYSTEM_LEGACY = """你是评测求解智能体（Learning Buddy C1 · SOLVE）。
任务：仅依据题干与课内材料，独立推导标准答案。
硬性规则：
1. 你看不到学生手写，reference_answer 必须是题目所求的最终结果。
2. 若题干给出全集 U，补集答案只能包含 U 内元素。
3. 不得抄写材料中的最终数值答案。
4. solution_steps 最多 4 条，每条不超过 80 字；JSON 内用 Unicode（π、√、∞）。
只输出 JSON：reference_answer, solution_steps[], method_id, shortcuts_used, solver_confidence, kp_id"""

SOLVE_STRICT_SUFFIX = """
【重试】上次 reference_answer 与学生手写过于接近，视为无效。
必须独立求解；reference_answer 必须是与手写不同的最终结果。"""

JUDGE_SYSTEM = """你是评测判题智能体（Learning Buddy C1 · JUDGE）。
比较「学生手写」与「参考标准答+解题步骤」，判断课内数学/语义等价（非字面相等）。
硬性规则：以「参考标准答」和解题步骤的最终结论为准；不得臆造学生未写的内容。
只输出 JSON（reasoning_steps 最多 4 条，每条 ≤80 字；勿用反斜杠 LaTeX）：
- is_wrong: boolean|null
- eval_confidence: number 0-1
- error_type: concept|careless|incomplete|unreadable|null
- reasoning_steps: string[]
- match_notes: string
- kp_id: string|null"""


def _extract_answer_from_steps(steps: list[str]) -> str | None:
    text = " ".join(steps)
    if not text.strip():
        return None
    patterns = [
        r"综合得\s*[AĀA\u0304\u0305̅]*\s*[=＝]\s*([^。；;]+)",
        r"故\s*[AĀA\u0304\u0305̅]*\s*[=＝]\s*([^。；;]+)",
        r"补集\s*[AĀA\u0304\u0305̅]*\s*[=＝为是：:]\s*([^。；;]+)",
        r"解集[为是：:]\s*([^。；;]+)",
        r"标准答案[为是：:]\s*([^。；;]+)",
        r"最终结果[为是：:]\s*([^。；;]+)",
        r"最终答案[为是：:]\s*([^。；;]+)",
    ]
    for pat in patterns:
        matches = list(re.finditer(pat, text, re.I))
        if matches:
            found = matches[-1].group(1).strip().strip("。")
            if found:
                return found
    return None


def _reference_violates_universe(reference: str, print_text: str) -> bool:
    ref = (reference or "").replace(" ", "")
    u_match = re.search(r"全集\s*U\s*[=＝]\s*(\([^)]+\)|\[[^\]]+\])", print_text)
    if not u_match:
        return False
    u = u_match.group(1).replace(" ", "")
    if re.search(r"\(-∞,1\)", u):
        if "∪{1}" in ref or "{1}" in ref:
            return True
        if "∪[1" in ref or "∪(1," in ref or "[1,+∞)" in ref:
            return True
    return False


def _answers_structurally_different(correct: str, wrong: str) -> bool:
    c = (correct or "").strip()
    w = (wrong or "").strip()
    if not c or not w or c == w:
        return False
    if "∪" in w and "∪" not in c:
        return True
    if len(w) > len(c) + 2 and c in w:
        return True
    return not answers_too_similar(c, w, threshold=0.92)


def _normalize_steps(raw: Any) -> list[str]:
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()][:6]
    return []


def _normalize_solve_payload(data: dict[str, Any]) -> dict[str, Any]:
    data["solution_steps"] = _normalize_steps(data.get("solution_steps"))
    ref = (data.get("reference_answer") or "").strip()
    if ref:
        data["reference_answer"] = ref
    return data


def _align_reference_with_steps(ref: dict[str, Any], print_text: str) -> dict[str, Any]:
    if not settings.eval_use_bank_answer:
        return ref
    ref_ans = str(ref.get("reference_answer") or "").strip()
    step_ans = _extract_answer_from_steps(ref.get("solution_steps") or [])
    if step_ans and ref_ans and _answers_structurally_different(step_ans, ref_ans):
        logger.info("align reference_answer %r -> %r from solution_steps", ref_ans, step_ans)
        ref["reference_answer"] = step_ans
    return ref


def _finalize_reference(
    ref: dict[str, Any],
    *,
    print_text: str,
    bank_answer: str | None,
    bank_source: str,
    bank_score: float,
    trace: EvalTraceBuilder | None = None,
) -> dict[str, Any]:
    rules: list[str] = []
    ref_before = str(ref.get("reference_answer") or "")
    ref = _align_reference_with_steps(ref, print_text)
    step_ans = _extract_answer_from_steps(ref.get("solution_steps") or [])
    ref_ans = str(ref.get("reference_answer") or "").strip()

    if settings.eval_use_bank_answer and step_ans and ref_ans and _answers_structurally_different(step_ans, ref_ans):
        logger.info("steps override reference_answer %r -> %r", ref_ans, step_ans)
        ref["reference_answer"] = step_ans
        ref_ans = step_ans
        rules.append("steps_override")

    if settings.eval_use_bank_answer and bank_answer and bank_score >= _BANK_STEM_TRUST:
        if _answers_structurally_different(bank_answer, ref_ans):
            ref["reference_answer"] = bank_answer
            ref["reference_source"] = bank_source or "question_bank"
            ref_ans = bank_answer
            rules.append("bank_override")

    if _reference_violates_universe(ref_ans, print_text):
        fix = (bank_answer if settings.eval_use_bank_answer else None) or step_ans
        if fix and not _reference_violates_universe(fix, print_text):
            ref["reference_answer"] = fix
            rules.append("universe_fix")
            if bank_answer and fix == bank_answer:
                ref["reference_source"] = bank_source or "question_bank"

    if trace is not None:
        trace.start_stage("finalize_reference")
        trace.set_input({"reference_before": ref_before})
        trace.set_output({"reference_after": ref.get("reference_answer"), "rules_applied": rules})
        trace.finish_stage()

    return ref


def _reconcile_judge_with_reference(
    judge: dict[str, Any],
    *,
    handwriting: str,
    reference_answer: str,
) -> dict[str, Any]:
    hw = (handwriting or "").strip()
    ref = (reference_answer or "").strip()
    if not hw or not ref:
        return judge
    if judge.get("is_wrong") and normalize_answer(ref) == normalize_answer(hw):
        judge = dict(judge)
        judge["is_wrong"] = False
        judge["match_notes"] = "与标准答一致（自动校正）"
        judge["eval_confidence"] = min(float(judge.get("eval_confidence", 0.7)), 0.85)
    return judge


def _parse_solve(raw: str) -> dict[str, Any]:
    return _normalize_solve_payload(
        parse_json_block_robust(raw, salvage_fields=_SOLVE_SALVAGE),
    )


def _parse_judge(raw: str) -> dict[str, Any]:
    data = parse_json_block_robust(raw, salvage_fields=_JUDGE_SALVAGE)
    data["reasoning_steps"] = _normalize_steps(data.get("reasoning_steps"))
    return data


def _think_user(print_text: str, hints_text: str, subject: str, grade_band: str) -> str:
    return (
        f"subject={subject} grade={grade_band}\n"
        f"题干: {print_text}\n"
        f"课内材料:\n{hints_text}\n"
        "请逐步推导并给出最终答案。"
    )


def solve_think(
    *,
    print_text: str,
    hints_text: str,
    subject: str,
    grade_band: str,
    trace: EvalTraceBuilder,
) -> ChatCompletionResult | None:
    if not settings.eval_agent_enabled:
        return None
    user = _think_user(print_text, hints_text, subject, grade_band)
    thinking = settings.eval_solve_thinking
    trace.start_stage("solve_think")
    trace.set_input({"system": SOLVE_THINK_SYSTEM, "user": user, "enable_thinking": thinking})
    t0 = time.perf_counter()
    try:
        result = chat_completion_detail(
            SOLVE_THINK_SYSTEM,
            user,
            max_tokens=2048,
            enable_thinking=thinking,
        )
        ms = int((time.perf_counter() - t0) * 1000)
        trace.set_output(
            {
                "reasoning_content": result.reasoning_content,
                "content": result.content,
                "reasoning_len": len(result.reasoning_content or ""),
                "content_len": len(result.content or ""),
                "usage": result.usage,
            }
        )
        trace.finish_stage(ms)
        return result
    except Exception as e:
        trace.set_output({"error": str(e)[:240]})
        trace.finish_stage(int((time.perf_counter() - t0) * 1000))
        logger.warning("solve_think failed: %s", e)
        return None


def solve_format(
    *,
    think_text: str,
    subject: str,
    grade_band: str,
    trace: EvalTraceBuilder,
) -> dict[str, Any] | None:
    user = (
        f"subject={subject} grade={grade_band}\n"
        f"求解推导:\n{think_text}\n"
        "输出 JSON。"
    )
    trace.start_stage("solve_format")
    trace.set_input({"system": SOLVE_FORMAT_SYSTEM, "user": user, "enable_thinking": False})
    t0 = time.perf_counter()
    try:
        raw = chat_completion(SOLVE_FORMAT_SYSTEM, user, max_tokens=1024, enable_thinking=False)
        data = _parse_solve(raw)
        data.setdefault("reference_source", "agent_solved")
        ms = int((time.perf_counter() - t0) * 1000)
        trace.set_output({"parsed": data, "reference_answer": data.get("reference_answer")})
        trace.finish_stage(ms)
        return data
    except Exception as e:
        trace.set_output({"error": str(e)[:240]})
        trace.finish_stage(int((time.perf_counter() - t0) * 1000))
        logger.warning("solve_format failed: %s", e)
        salvaged = salvage_json_fields(think_text, _SOLVE_SALVAGE)
        if salvaged.get("reference_answer"):
            return _normalize_solve_payload(salvaged)
        return None


def solve_reference_legacy(
    *,
    print_text: str,
    hints_text: str,
    subject: str,
    grade_band: str,
    reference_answer: str | None = None,
    bank_source: str = "",
    bank_stem_score: float = 0.0,
    strict: bool = False,
    trace: EvalTraceBuilder | None = None,
) -> dict | None:
    if not settings.eval_agent_enabled:
        return None
    bank_line = (
        f"题库标准答（{bank_source}）: {reference_answer}"
        if reference_answer and settings.eval_use_bank_answer
        else ""
    )
    user = (
        f"subject={subject} grade={grade_band}\n"
        f"题干: {print_text}\n"
        f"课内材料:\n{hints_text}\n"
        f"{bank_line}\n"
        "输出 JSON。"
    )
    if strict:
        user += SOLVE_STRICT_SUFFIX
    thinking = settings.eval_solve_thinking
    if trace is not None:
        trace.start_stage("solve_think")
        trace.set_input({"system": SOLVE_SYSTEM_LEGACY, "user": user, "enable_thinking": thinking})
    t0 = time.perf_counter()
    try:
        raw = chat_completion(SOLVE_SYSTEM_LEGACY, user, max_tokens=1536, enable_thinking=thinking)
        if trace is not None:
            trace.set_output({"content": raw, "content_len": len(raw)})
            trace.finish_stage(int((time.perf_counter() - t0) * 1000))
        data = _parse_solve(raw)
        if reference_answer and settings.eval_use_bank_answer and bank_stem_score >= _BANK_STEM_TRUST:
            data["reference_answer"] = reference_answer
            data["reference_source"] = bank_source or "question_bank"
        else:
            data.setdefault("reference_source", "agent_solved")
        return data
    except Exception as e:
        if trace is not None:
            trace.set_output({"error": str(e)[:240]})
            trace.finish_stage(int((time.perf_counter() - t0) * 1000))
        logger.warning("solve_reference_legacy failed: %s", e)
        return None


def _run_solve(
    *,
    print_text: str,
    hints_text: str,
    subject: str,
    grade_band: str,
    reference_answer: str | None,
    bank_source: str,
    bank_stem_score: float,
    strict: bool,
    trace: EvalTraceBuilder,
) -> dict | None:
    if settings.eval_two_phase_solve:
        think = solve_think(
            print_text=print_text,
            hints_text=hints_text,
            subject=subject,
            grade_band=grade_band,
            trace=trace,
        )
        if not think:
            return None
        combined = think.content or ""
        if think.reasoning_content:
            combined = f"{think.reasoning_content}\n\n{combined}".strip()
        return solve_format(
            think_text=combined,
            subject=subject,
            grade_band=grade_band,
            trace=trace,
        )
    return solve_reference_legacy(
        print_text=print_text,
        hints_text=hints_text,
        subject=subject,
        grade_band=grade_band,
        reference_answer=reference_answer,
        bank_source=bank_source,
        bank_stem_score=bank_stem_score,
        strict=strict,
        trace=trace,
    )


def judge_against_reference(
    *,
    print_text: str,
    handwriting: str,
    reference: dict[str, Any],
    subject: str,
    grade_band: str,
    trace: EvalTraceBuilder | None = None,
) -> dict | None:
    if not settings.eval_agent_enabled:
        return None
    ref = dict(reference)
    steps = ref.get("solution_steps") or []
    user = (
        f"subject={subject} grade={grade_band}\n"
        f"题干: {print_text}\n"
        f"参考标准答: {ref.get('reference_answer')}\n"
        f"标准解题步骤: {steps}\n"
        f"学生手写: {handwriting or '(无)'}\n"
        "输出 JSON（含 reasoning_steps 逐步推理）。"
    )
    if trace is not None:
        trace.start_stage("judge")
        trace.set_input(
            {
                "print_text": print_text,
                "reference_answer": ref.get("reference_answer"),
                "handwriting": handwriting,
                "enable_thinking": False,
            }
        )
    t0 = time.perf_counter()
    last_err: Exception | None = None
    for attempt, max_tok in enumerate((768, 512)):
        try:
            raw = chat_completion(JUDGE_SYSTEM, user, max_tokens=max_tok, enable_thinking=False)
            parsed = _parse_judge(raw)
            if trace is not None:
                trace.set_output(
                    {
                        "is_wrong": parsed.get("is_wrong"),
                        "eval_confidence": parsed.get("eval_confidence"),
                        "reasoning_steps": parsed.get("reasoning_steps"),
                    }
                )
                trace.finish_stage(int((time.perf_counter() - t0) * 1000))
            return parsed
        except Exception as e:
            last_err = e
            logger.warning("judge attempt %d failed: %s", attempt + 1, e)
    if trace is not None:
        trace.set_output({"error": str(last_err)[:240] if last_err else "unknown"})
        trace.finish_stage(int((time.perf_counter() - t0) * 1000))
    return None


def _ensure_valid_reference(
    ref: dict[str, Any],
    *,
    print_text: str,
    handwriting: str,
    hints_text: str,
    subject: str,
    grade_band: str,
    bank_answer: str | None,
    bank_source: str,
    bank_stem_score: float = 0.0,
    trace: EvalTraceBuilder | None = None,
) -> dict[str, Any]:
    hw = (handwriting or "").strip()
    ref_ans = str(ref.get("reference_answer") or "").strip()
    retried = False
    if hw and ref_ans and answers_too_similar(ref_ans, hw):
        retried = True
        retry = _run_solve(
            print_text=print_text,
            hints_text=hints_text,
            subject=subject,
            grade_band=grade_band,
            reference_answer=bank_answer if bank_answer and not answers_too_similar(bank_answer, hw) else None,
            bank_source=bank_source,
            bank_stem_score=bank_stem_score,
            strict=True,
            trace=trace or EvalTraceBuilder(),
        )
        if retry and retry.get("reference_answer"):
            ref = retry
        elif bank_answer and settings.eval_use_bank_answer and not answers_too_similar(bank_answer, hw):
            ref["reference_answer"] = bank_answer
            ref["reference_source"] = bank_source or "rag_bank"
    if trace is not None:
        trace.start_stage("ensure_valid_reference")
        trace.set_output({"retried": retried, "reference_after": ref.get("reference_answer")})
        trace.finish_stage()
    return ref


def evaluate_question_full(
    *,
    print_text: str,
    handwriting: str,
    hints_text: str,
    hints_meta: dict[str, Any] | None = None,
    rag_meta: dict[str, Any] | None = None,
    subject: str,
    grade_band: str,
    reference_answer: str | None = None,
    bank_source: str = "",
    bank_stem_score: float = 0.0,
) -> dict | None:
    trace = EvalTraceBuilder()

    if rag_meta:
        trace.start_stage("rag_retrieve")
        trace.set_input({"print_text": print_text, "subject": subject, "grade_band": grade_band})
        trace.set_output(rag_meta)
        trace.finish_stage()

    trace.start_stage("solve_hints")
    trace.set_output(
        {
            "hints_text": hints_text,
            "hints_meta": hints_meta or {},
            "hints_len": len(hints_text or ""),
        }
    )
    trace.finish_stage()

    hw = (handwriting or "").strip()
    bank = reference_answer if settings.eval_use_bank_answer else None
    bank_src = bank_source if settings.eval_use_bank_answer else ""
    bank_score = bank_stem_score if settings.eval_use_bank_answer else 0.0
    if bank and hw and answers_too_similar(bank, hw):
        bank = None
        bank_src = ""
        bank_score = 0.0

    ref = _run_solve(
        print_text=print_text,
        hints_text=hints_text,
        subject=subject,
        grade_band=grade_band,
        reference_answer=bank,
        bank_source=bank_src,
        bank_stem_score=bank_score,
        strict=False,
        trace=trace,
    )
    if not ref or not ref.get("reference_answer"):
        if settings.eval_fallback_rules and not hw:
            out = {"is_wrong": None, "eval_confidence": 0.0, "error_type": "unreadable"}
            out["eval_trace"] = trace.to_dict()
            out["prompt_version"] = PROMPT_VERSION
            return out
        failed = {"eval_status": "failed", "prompt_version": PROMPT_VERSION, "eval_trace": trace.to_dict()}
        return failed

    ref = _finalize_reference(
        ref,
        print_text=print_text,
        bank_answer=bank,
        bank_source=bank_src,
        bank_score=bank_score,
        trace=trace,
    )

    ref = _ensure_valid_reference(
        ref,
        print_text=print_text,
        handwriting=handwriting,
        hints_text=hints_text,
        subject=subject,
        grade_band=grade_band,
        bank_answer=bank,
        bank_source=bank_src,
        bank_stem_score=bank_score,
        trace=trace,
    )

    ref = _finalize_reference(
        ref,
        print_text=print_text,
        bank_answer=bank,
        bank_source=bank_src,
        bank_score=bank_score,
        trace=trace,
    )

    judge = judge_against_reference(
        print_text=print_text,
        handwriting=handwriting,
        reference=ref,
        subject=subject,
        grade_band=grade_band,
        trace=trace,
    )
    if not judge:
        out = {"eval_status": "failed", "prompt_version": PROMPT_VERSION, "eval_trace": trace.to_dict()}
        return out

    judge = _reconcile_judge_with_reference(
        judge,
        handwriting=handwriting,
        reference_answer=str(ref.get("reference_answer") or ""),
    )

    solver_c = float(ref.get("solver_confidence", 0.6))
    compare_c = float(judge.get("eval_confidence", 0.6))
    rag_boost = 1.0 if ref.get("reference_source", "").startswith(("rag", "question_bank")) else 0.92
    eval_confidence = min(solver_c, compare_c) * rag_boost

    threshold = settings.eval_confidence_threshold
    agent = judge.get("is_wrong")
    wrong_source = "pending"
    if agent is True and compare_c >= threshold:
        wrong_source = "eval"
    elif agent is False and compare_c >= threshold:
        wrong_source = "eval"

    trace.start_stage("apply_eval")
    trace.set_input({"threshold": threshold})
    trace.set_output(
        {
            "is_wrong": agent,
            "wrong_source": wrong_source,
            "eval_confidence": round(eval_confidence, 4),
        }
    )
    trace.finish_stage()

    out = {
        **judge,
        **{k: v for k, v in ref.items() if k not in judge},
        "eval_confidence": round(eval_confidence, 4),
        "reference_source": ref.get("reference_source"),
        "kp_confidence": compare_c,
        "prompt_version": PROMPT_VERSION,
        "eval_status": "completed",
        "eval_trace": trace.to_dict(),
    }
    return out

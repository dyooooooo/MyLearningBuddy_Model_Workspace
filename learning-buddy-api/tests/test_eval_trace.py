from __future__ import annotations

import json
from unittest.mock import patch

from app.agents.eval_agent import evaluate_question_full
from app.agents.eval_trace import EvalTraceBuilder
from app.clients.vllm import ChatCompletionResult
from app.config import settings


def test_eval_trace_builder_stages():
    trace = EvalTraceBuilder()
    trace.start_stage("solve_think")
    trace.set_input({"enable_thinking": True})
    trace.set_output({"reasoning_content": "r", "content": "c"})
    trace.finish_stage(100)
    d = trace.to_dict(full=True)
    assert d["trace_version"] == 1
    assert d["prompt_version"] == "eval_v4_hints"
    assert any(s["stage"] == "solve_think" for s in d["stages"])
    think = next(s for s in d["stages"] if s["stage"] == "solve_think")
    assert think["input"]["enable_thinking"] is True


@patch("app.agents.eval_agent.chat_completion")
@patch("app.agents.eval_agent.chat_completion_detail")
def test_evaluate_full_pipeline_trace_stages(mock_detail, mock_chat):
    mock_detail.return_value = ChatCompletionResult(
        content="最终答案：(-∞,-1]",
        reasoning_content="逐步推导补集",
    )
    fmt_json = json.dumps(
        {
            "reference_answer": "(-∞,-1]",
            "solution_steps": ["综合得 A̅ = (-∞,-1]。"],
            "solver_confidence": 0.9,
            "kp_id": "math.hs.sets.complement",
        },
        ensure_ascii=False,
    )
    judge_json = json.dumps(
        {
            "is_wrong": True,
            "eval_confidence": 0.82,
            "reasoning_steps": ["手写含 1，超出全集"],
            "error_type": "concept",
        },
        ensure_ascii=False,
    )
    mock_chat.side_effect = [fmt_json, judge_json]

    with patch.object(settings, "eval_two_phase_solve", True):
        out = evaluate_question_full(
            print_text="已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为",
            handwriting="包含 1 的错误区间",
            hints_text="【知识点】补集",
            rag_meta={"entry_node_ids": ["kp1"], "kp_count": 1},
            subject="math",
            grade_band="hs",
        )

    assert out is not None
    trace = out.get("eval_trace") or {}
    stages = [s["stage"] for s in trace.get("stages", [])]
    assert trace.get("trace_version") == 1
    assert "solve_think" in stages
    assert "solve_format" in stages
    assert "judge" in stages
    assert "apply_eval" in stages
    think = next(s for s in trace["stages"] if s["stage"] == "solve_think")
    assert think["input"]["enable_thinking"] is True
    mock_detail.assert_called_once()

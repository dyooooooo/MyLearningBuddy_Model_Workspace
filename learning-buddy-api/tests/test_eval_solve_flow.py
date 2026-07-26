from unittest.mock import patch

from app.agents.eval_agent import _run_solve, solve_think
from app.agents.eval_trace import EvalTraceBuilder
from app.clients.vllm import ChatCompletionResult
from app.config import settings


@patch("app.agents.eval_agent.chat_completion_detail")
def test_solve_think_passes_enable_thinking(mock_detail):
    mock_detail.return_value = ChatCompletionResult(content="c", reasoning_content="r")
    trace = EvalTraceBuilder()
    with patch.object(settings, "eval_solve_thinking", True):
        solve_think(
            print_text="题干",
            hints_text="hints",
            subject="math",
            grade_band="hs",
            trace=trace,
        )
    _, kwargs = mock_detail.call_args
    assert kwargs.get("enable_thinking") is True


@patch("app.agents.eval_agent.solve_format")
@patch("app.agents.eval_agent.solve_think")
def test_run_solve_two_phase_order(mock_think, mock_format):
    mock_think.return_value = ChatCompletionResult(content="ans", reasoning_content="why")
    mock_format.return_value = {"reference_answer": "1"}
    trace = EvalTraceBuilder()
    with patch.object(settings, "eval_two_phase_solve", True):
        out = _run_solve(
            print_text="q",
            hints_text="h",
            subject="math",
            grade_band="hs",
            reference_answer=None,
            bank_source="",
            bank_stem_score=0.0,
            strict=False,
            trace=trace,
        )
    assert out == {"reference_answer": "1"}
    mock_think.assert_called_once()
    mock_format.assert_called_once()

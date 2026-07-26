"""Per-question eval pipeline trace (stored in eval_json.eval_trace)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.config import settings

TRACE_PROMPT_VERSION = "eval_v4_hints"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvalTraceBuilder:
    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._started_at = _now_iso()
        self._stages: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._stage_t0: float = 0.0

    def start_stage(self, name: str) -> None:
        if self._current is not None:
            self.finish_stage()
        self._current = {"stage": name, "input": {}, "output": {}}
        self._stage_t0 = time.perf_counter()

    def set_input(self, data: dict[str, Any]) -> None:
        if self._current is not None:
            self._current["input"] = data

    def set_output(self, data: dict[str, Any]) -> None:
        if self._current is not None:
            self._current["output"] = data

    def finish_stage(self, ms: int | None = None) -> None:
        if self._current is None:
            return
        if ms is None:
            ms = int((time.perf_counter() - self._stage_t0) * 1000)
        self._current["ms"] = ms
        self._stages.append(self._current)
        self._current = None

    def log_stage_summary(self, question_id: str, stage: str, ms: int, detail: str = "") -> None:
        if settings.eval_debug_log:
            import logging

            logging.getLogger(__name__).info(
                "eval_trace question=%s stage=%s ms=%s %s",
                question_id,
                stage,
                ms,
                detail,
            )

    def to_dict(self, *, full: bool | None = None) -> dict[str, Any]:
        if self._current is not None:
            self.finish_stage()
        use_full = settings.eval_debug_trace if full is None else full
        stages = self._stages
        if not use_full:
            stages = [
                {
                    "stage": s.get("stage"),
                    "ms": s.get("ms"),
                    "output": _summarize_output(s),
                }
                for s in self._stages
            ]
        total_ms = int((time.perf_counter() - self._started) * 1000)
        return {
            "trace_version": 1,
            "prompt_version": TRACE_PROMPT_VERSION,
            "started_at": self._started_at,
            "total_ms": total_ms,
            "config_snapshot": {
                "eval_solve_thinking": settings.eval_solve_thinking,
                "eval_two_phase_solve": settings.eval_two_phase_solve,
                "eval_use_bank_answer": settings.eval_use_bank_answer,
                "eval_hints_max_per_section": settings.eval_hints_max_per_section,
            },
            "stages": stages,
        }


def _summarize_output(stage: dict[str, Any]) -> dict[str, Any]:
    out = stage.get("output") or {}
    if not isinstance(out, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "reference_answer",
        "is_wrong",
        "eval_confidence",
        "hints_len",
        "reasoning_len",
        "content_len",
        "retried",
        "rules_applied",
        "wrong_source",
    ):
        if key in out:
            summary[key] = out[key]
    return summary

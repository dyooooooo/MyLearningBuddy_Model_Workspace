"""v4 校验面（35B 线自有，禁止被 MinerU 线 import）。

T1：确定性规则（同步，编译后立即跑）——见 ``rules.py``。
T2：Agent 视觉比对（异步，仅产疑点）——预留占位，见 ``agent_checker.py``。
"""
from app.import_35b.validation.agent_checker import (
    agent_check_enabled,
    run_agent_checks,
)
from app.import_35b.validation.rules import Finding, run_page_checks

__all__ = ["Finding", "run_page_checks", "run_agent_checks", "agent_check_enabled"]

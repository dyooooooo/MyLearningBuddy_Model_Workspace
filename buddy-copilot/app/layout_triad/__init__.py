"""三角裁决：35B 主力视觉归类 + MinerU/结构锚点 + 差异 escalate。"""

from .compare import compare_page, compare_pages
from .triad import merged_to_page_plan_hints, run_triad

__all__ = [
    "compare_page",
    "compare_pages",
    "merged_to_page_plan_hints",
    "run_triad",
]

"""三角裁决共用版面信号（35B / MinerU 锚点 / compare）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal[
    "目录",
    "正文单栏",
    "正文双栏",
    "正文多栏",
    "侧栏",
    "习题",
    "图版",
    "公式密集",
    "封面/扉页",
    "空白/其它",
]


class PageLayoutSignals(BaseModel):
    """单页版面归类信号（双方对齐后再 compare）。"""

    page: int
    category: str = "空白/其它"
    columns: int = 1  # 1/2/3+
    aside: bool = False
    toc_like: bool = False
    exercise_like: bool = False
    figure_heavy: bool = False
    math_dense: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str = ""
    source: str = ""  # judge_35b | mineru_cloud | structural | …

    def signal_tuple(self) -> tuple[Any, ...]:
        """用于差异比较的关键字段。"""
        cols = 3 if self.columns >= 3 else max(1, int(self.columns))
        return (
            self.category,
            cols,
            bool(self.aside),
            bool(self.toc_like),
            bool(self.exercise_like),
            bool(self.figure_heavy),
            bool(self.math_dense),
        )


class PageCompareResult(BaseModel):
    page: int
    agree: bool
    escalate: bool  # True → 建议 Cursor 视觉 Read
    judge: PageLayoutSignals
    anchor: PageLayoutSignals
    diffs: list[str] = Field(default_factory=list)
    merged: PageLayoutSignals  # 采纳建议（agree 时偏 35B，escalate 时仍给 35B 作草案）

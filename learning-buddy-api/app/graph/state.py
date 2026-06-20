from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class GraphState(TypedDict):
    student_id: str
    homework_id: str
    run_id: str
    subject: str
    grade_band: str
    status: str
    steps: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]

from app.rag.retriever import EvalContext
from app.rag.solve_hints import build_solve_hints


def test_solve_hints_excludes_questions_and_answers():
    ctx = EvalContext(
        query="补集",
        subject="math",
        grade_band="hs",
        kps=[{"id": "kp1", "label": "补集", "summary": "A 的补集"}],
        methods=[{"id": "m1", "label": "补集法", "text": "U 中不属于 A 的元素"}],
        shortcuts=[{"id": "s1", "label": "口诀", "formula": "Ā = U \\ A"}],
        chunks=[{"id": "c1", "label": "定义", "summary": "补集定义"}],
        questions=[{"id": "q1", "label": "例题", "meta": {"answer": "(-∞,-1]"}}],
        hits=[],
        entry_node_ids=["kp1"],
    )
    hints = build_solve_hints(ctx)
    text = hints.hints_text
    assert "【知识点】" in text
    assert "【解法】" in text
    assert "不得抄写" in text
    assert "(-∞,-1]" not in text
    assert hints.hints_meta["kp_count"] == 1


def test_solve_hints_strips_answer_from_meta():
    ctx = EvalContext(
        query="x",
        subject="math",
        grade_band="hs",
        methods=[
            {
                "id": "m1",
                "label": "法",
                "text": "步骤",
                "meta": {"answer": "secret"},
            }
        ],
    )
    hints = build_solve_hints(ctx)
    assert "secret" not in hints.hints_text

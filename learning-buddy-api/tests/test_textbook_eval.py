from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, HomeworkEntry, QuestionEntity, Student, TextbookEvalRequest
from app.services.textbook_eval import apply_eval_callback, call_textbook_agent


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(session):
    session.add(Student(id="s1", label="t", active=True, api_token_hash="x"))
    session.add(
        HomeworkEntry(
            id="hw1",
            student_id="s1",
            session_id="sess",
            page_id="p1",
            subject="math",
            grade_band="hs",
            finalized_at="t",
        )
    )
    session.add(
        QuestionEntity(
            id="hw1__q__1",
            student_id="s1",
            homework_id="hw1",
            ordinal="1",
            print_text="用列举法表示所有不大于10的正整数组成的集合",
            handwriting="{0,1,2,3,4,5,6,7,8,9,10}",
        )
    )
    session.add(
        TextbookEvalRequest(
            request_id="lb-20260819-0001",
            question_id="hw1__q__1",
            homework_id="hw1",
            student_id="s1",
            status="pending",
        )
    )
    session.commit()


def test_eval_callback_marks_wrong_and_stores_textbook():
    session = _session()
    _seed(session)
    apply_eval_callback(
        session,
        {
            "request_id": "lb-20260819-0001",
            "correct": False,
            "verdict": "错",
            "correct_solution": "不大于10的正整数是{1,2,3,4,5,6,7,8,9,10}，0不是正整数。",
            "textbook": {
                "title": "普通高中教科书 数学 必修第一册",
                "chapter": "第1章 集合与逻辑",
                "section": "1.1 集合初步",
                "examples": ["例3"],
            },
            "book_missing": False,
            "notes": "学生把0算进正整数。",
        },
    )
    session.commit()
    q = session.get(QuestionEntity, "hw1__q__1")
    assert q is not None
    assert q.is_wrong is True
    ev = q.eval_json
    assert "1.1" in ev
    assert "例3" in ev
    assert "0不是正整数" in ev
    assert "cursor_agent" in ev
    req = session.get(TextbookEvalRequest, "lb-20260819-0001")
    assert req is not None and req.status == "completed"
    session.close()


def test_eval_callback_book_missing_does_not_fake_grade():
    session = _session()
    _seed(session)
    apply_eval_callback(
        session,
        {
            "request_id": "lb-20260819-0001",
            "correct": None,
            "book_missing": True,
            "notes": "教材未覆盖",
        },
    )
    session.commit()
    q = session.get(QuestionEntity, "hw1__q__1")
    assert q is not None
    assert q.is_wrong is None
    assert "book_missing" in q.eval_json
    session.close()


def test_call_textbook_agent_is_http_only():
    session = _session()
    _seed(session)
    post = MagicMock()
    post.json.return_value = {"job_id": "job-1"}
    post.raise_for_status = MagicMock()
    get = MagicMock()
    get.json.return_value = {
        "status": "completed",
        "result": {
            "request_id": "lb-20260819-0001",
            "correct": False,
            "verdict": "错",
            "correct_solution": "正解",
            "textbook": {"section": "1.1 集合初步"},
            "book_missing": False,
        },
    }
    get.raise_for_status = MagicMock()
    fake = MagicMock()
    fake.__enter__.return_value = fake
    fake.__exit__.return_value = False
    fake.post.return_value = post
    fake.get.return_value = get
    with (
        patch("app.services.textbook_eval.settings") as st,
        patch("app.services.textbook_eval.httpx.Client", return_value=fake),
    ):
        st.textbook_agent_url = "http://textbook-agent:8200"
        st.textbook_agent_token = "tok"
        st.textbook_agent_timeout_s = 30.0
        call_textbook_agent(session, "lb-20260819-0001")
    fake.post.assert_called_once()
    assert fake.post.call_args.args[0] == "http://textbook-agent:8200/v1/grade"
    q = session.get(QuestionEntity, "hw1__q__1")
    assert q is not None and q.is_wrong is True
    session.close()

from app.services.eval_traces import build_homework_eval_traces


def test_build_homework_eval_traces_none_when_missing():
    class _S:
        def get(self, _model, _id):
            return None

    assert build_homework_eval_traces(_S(), "s1", "hw1") is None

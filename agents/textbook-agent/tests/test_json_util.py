from app.json_util import extract_json_object


def test_extract_fenced_json() -> None:
    data = extract_json_object('前言\n```json\n{"correct": false, "book_missing": false}\n```\n')
    assert data["correct"] is False


def test_extract_raw_object() -> None:
    data = extract_json_object('{"request_id": "a", "book_missing": true}')
    assert data["request_id"] == "a"

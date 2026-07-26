"""Tests for debug bundle index loader."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.import_35b.debug_bundle import load_page_debug_index


def _job():
    return SimpleNamespace(
        id="job-abc",
        minio_key="import35b/job-abc/source.pdf",
    )


def test_debug_index_marks_available_artifacts(monkeypatch):
    keys: dict[str, bytes] = {
        "import35b/job-abc/assets/35b/debug/p0001/raw_model.json": b'{"page_no":1}',
        "import35b/job-abc/assets/35b/debug/p0001/debug.json": b'{"status":"ok"}',
        "import35b/job-abc/assets/35b/debug/p0001/stage_changelog.json": json.dumps(
            {"page_no": 1, "total_changes": 0, "by_transition": {}, "summary_zh": "无"},
        ).encode(),
    }

    def fake_get(key: str):
        return keys.get(key)

    monkeypatch.setattr("app.import_35b.debug_bundle.get_object_bytes", fake_get)
    idx = load_page_debug_index(_job(), 1)
    assert idx["artifacts"]["raw_model"]["available"] is True
    assert idx["artifacts"]["post_repair"]["available"] is False
    assert idx["changelog_summary"]["total_changes"] == 0

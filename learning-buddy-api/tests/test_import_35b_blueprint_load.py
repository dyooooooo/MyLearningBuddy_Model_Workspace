"""Blueprint load fallbacks for HITL source inspector."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.import_35b.review_service import _load_page_blueprint_dict


def _job(**meta):
    return SimpleNamespace(
        minio_key="import35b/job-abc/source.pdf",
        meta_json=json.dumps(meta),
    )


@pytest.mark.parametrize(
    "key,data,page_no,expected_id",
    [
        (
            "import35b/job-abc/assets/35b/blueprints/p0002.json",
            b'{"page_no":2,"blocks":[{"id":"p2_b1"}]}',
            2,
            "p2_b1",
        ),
        (
            "import35b/job-abc/assets/35b/debug/p0002/blueprint.json",
            b'{"page_no":2,"blocks":[{"id":"dbg_b1"}]}',
            2,
            "dbg_b1",
        ),
    ],
)
def test_load_page_blueprint_fallback(monkeypatch, key, data, page_no, expected_id):
    def fake_get(k: str):
        if k == key:
            return data
        if k.endswith("pages.json"):
            return None
        if k.endswith("p0002.json") and "blueprints/p0002" in k and key != k:
            return None
        return None

    monkeypatch.setattr("app.import_35b.review_service.get_object_bytes", fake_get)
    bp = _load_page_blueprint_dict(_job(), page_no)
    assert bp is not None
    assert bp["blocks"][0]["id"] == expected_id


def test_load_page_blueprint_from_aggregate_meta(monkeypatch):
    agg_key = "import35b/job-abc/assets/35b/blueprints/pages.json"
    agg = b'[{"page_no":1,"blocks":[]},{"page_no":3,"blocks":[{"id":"p3_x"}]}]'

    def fake_get(k: str):
        if k == agg_key:
            return agg
        return None

    monkeypatch.setattr("app.import_35b.review_service.get_object_bytes", fake_get)
    bp = _load_page_blueprint_dict(_job(blueprints_key=agg_key), 3)
    assert bp is not None
    assert bp["blocks"][0]["id"] == "p3_x"

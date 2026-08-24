"""改动权限类推断。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.authority import classify_authority, stamp_authority  # noqa: E402


def test_page_ir_is_edition():
    assert classify_authority(mode="cursor_ir") == "edition"
    assert classify_authority(mode="patch_block") == "edition"


def test_python_app_path_is_pipeline():
    assert (
        classify_authority(
            paths=["/home/x/learning-buddy-api/app/services/mineru_adapter.py"]
        )
        == "pipeline"
    )


def test_layout_rules_yaml_is_edition():
    assert (
        classify_authority(
            paths=["learning-buddy-api/configs/editions/foo/layout_rules.yaml"]
        )
        == "edition"
    )


def test_stamp_authority():
    e = stamp_authority({"summary": "x", "mode": "fs_violation"})
    assert e["authority"] == "pipeline"
    assert e["authority_label"] == "越权"

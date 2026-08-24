"""layout_change attempts expose tool/args for IP procedure folding."""
from __future__ import annotations

from app.segment_state import append_layout_change, procedure_hints_instruction


def test_append_layout_change_fills_tool_fields():
    pl: dict = {}
    pl["layout_change_log"] = append_layout_change(
        pl,
        {
            "op": "patch_block",
            "page": 3,
            "summary": "swap figures",
            "block_ids": ["a", "b"],
        },
    )
    item = pl["layout_change_log"][0]
    assert item["tool"] == "patch_block"
    assert "swap" in item["args_summary"]
    assert item["block_ids"] == ["a", "b"]


def test_procedure_hints_instruction():
    text = procedure_hints_instruction(
        {
            "improvement_procedures": [
                {
                    "page_no": 12,
                    "category": "figure",
                    "summary": "swap mispaired",
                    "procedure": {
                        "steps": [
                            {"tool": "corpus.get_blocks"},
                            {"tool": "corpus.patch_block"},
                        ]
                    },
                }
            ]
        }
    )
    assert "已知成功路径" in text
    assert "corpus.patch_block" in text
    assert "p12" in text

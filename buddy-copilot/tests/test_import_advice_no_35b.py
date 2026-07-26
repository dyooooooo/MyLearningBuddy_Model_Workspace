"""import_advice 禁止 35B reprocess modes。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.plugins.corpus_tools import build  # noqa: E402
from app.tools.registry import ToolContext  # noqa: E402


def _specs(kind: str):
    client = MagicMock()
    client.reprocess_page.return_value = {"ok": True, "mode": "layout_content"}
    ctx = ToolContext(
        settings=MagicMock(),
        store=MagicMock(),
        client=client,
        session_id="s1",
        kind=kind,
    )
    return {s.name: s for s in build(ctx)}, client


def test_import_advice_blocks_35b_reprocess():
    specs, client = _specs("import_advice")
    fn = specs["corpus.reprocess_page"].fn
    out = fn("job1", 3, "layout_content", "")
    assert out.get("ok") is False
    assert "禁用 35B" in str(out.get("error") or "")
    client.reprocess_page.assert_not_called()


def test_import_advice_allows_rerender_mode():
    specs, client = _specs("import_advice")
    client.reprocess_page.return_value = {
        "ok": True,
        "mode": "rerender",
        "persisted": True,
    }
    fn = specs["corpus.reprocess_page"].fn
    out = fn("job1", 3, "rerender", "")
    assert out.get("ok") is True
    client.reprocess_page.assert_called_once()


def test_page_assist_still_allows_35b():
    specs, client = _specs("page_assist")
    fn = specs["corpus.reprocess_page"].fn
    out = fn("job1", 3, "layout_rebuild", "fix columns")
    assert out.get("ok") is True
    client.reprocess_page.assert_called_once()


def test_import_advice_has_ir_tools():
    specs, _ = _specs("import_advice")
    for name in (
        "corpus.get_blocks",
        "corpus.patch_block",
        "corpus.replace_page_blocks",
        "corpus.rerender_page",
    ):
        assert name in specs

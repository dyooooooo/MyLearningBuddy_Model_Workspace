"""IR patch order + replace_page_blocks (no enrich scramble)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.document_ir import DocumentBlock, blocks_for_page, render_page_markdown
from app.services.import_blocks_service import patch_job_block, replace_page_blocks


def _block(i: int, page: int = 1, order: int | None = None) -> DocumentBlock:
    o = order if order is not None else i
    return DocumentBlock(
        id=f"b{i}",
        type="paragraph",
        page_no=page,
        order=o,
        text=f"t{i}",
        meta={"layout_zone": "main", "reading_order": o},
    )


def test_blocks_for_page_sorts_by_order_not_storage():
    """Storage array may keep MinerU detect order; render must follow ``order``."""
    blocks = [
        _block(2, page=6, order=2),
        _block(3, page=6, order=3),
        DocumentBlock(
            id="toc_title",
            type="heading",
            page_no=6,
            order=1,
            text="目录",
            meta={"layout_zone": "main", "reading_order": 1},
        ),
        _block(9, page=7, order=1),
    ]
    page = blocks_for_page(blocks, 6)
    assert [b.id for b in page] == ["toc_title", "b2", "b3"]
    assert [b.order for b in page] == [1, 2, 3]


def test_rendered_page_follows_order_after_a_single_block_patch():
    """改一个块的 order 就要改渲染顺序——补丁只写字段，不动存储数组位置。"""
    blocks = [_block(1, order=1), _block(2, order=2), _block(3, order=3)]
    moved = blocks[0].model_copy(update={"order": 9, "meta": {"reading_order": 9}})
    patched = [moved] + blocks[1:]

    assert [b.id for b in blocks_for_page(patched, 1)] == ["b2", "b3", "b1"]
    md = render_page_markdown(patched, 1)
    assert md.index("t2") < md.index("t3") < md.index("t1")


def test_patch_job_block_accepts_order():
    blocks = [_block(1), _block(2)]
    job = SimpleNamespace(id="j1", meta_json="{}")
    session = MagicMock()
    session.get.return_value = job
    with (
        patch("app.services.import_blocks_service.load_blocks", return_value=blocks),
        patch("app.services.import_blocks_service.save_blocks") as save,
        patch("app.services.import_blocks_service._job_meta", return_value={}),
        patch("app.services.import_blocks_service.dumps", return_value="{}"),
    ):
        _, out, info = patch_job_block(session, "j1", "b1", {"order": 9, "text": "x"})
    save.assert_called_once()
    by_id = {b["id"]: b for b in out}
    assert by_id["b1"]["order"] == 9
    assert by_id["b1"]["meta"]["reading_order"] == 9
    assert by_id["b1"]["text"] == "x"
    assert info["html_changed"] is True
    assert "x" in info["block_html"]


def test_replace_page_blocks_reorders():
    blocks = [_block(1), _block(2), _block(3, page=2)]
    job = SimpleNamespace(id="j1", meta_json="{}")
    session = MagicMock()
    session.get.return_value = job
    with (
        patch("app.services.import_blocks_service.load_blocks", return_value=blocks),
        patch("app.services.import_blocks_service.save_blocks") as save,
        patch("app.services.import_blocks_service._job_meta", return_value={}),
        patch("app.services.import_blocks_service.dumps", return_value="{}"),
        patch(
            "app.services.import_blocks_service.rerender_import_page",
            return_value="<p>ok</p>",
        ),
    ):
        _, page_blocks, html, _info = replace_page_blocks(
            session,
            "j1",
            1,
            [{"id": "b2"}, {"id": "b1"}],
            rerender=True,
        )
    assert html == "<p>ok</p>"
    assert [b["id"] for b in page_blocks] == ["b2", "b1"]
    assert page_blocks[0]["order"] == 1
    assert page_blocks[1]["order"] == 2
    save.assert_called()

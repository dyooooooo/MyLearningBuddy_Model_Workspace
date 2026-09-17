"""Re-render one imported page from the cached MinerU blocks.

`process_kb_book` 会先 `reset_derived` 删掉 `mineru/` 再重跑 MinerU 云端 OCR，所以
「只想修一页」不该走全量 convert。这里复用同一套渲染器（`blocks_to_page_markdown`
+ `tag_source`）喂缓存里的 `mineru/blocks.json`，产物与重导那一页完全一致。

用途：目录页被 `write_toc_pages` 的旧版本覆盖（插图丢失、混入正文小节）后就地修回。

    python scripts/repair_kb_page_from_cache.py <book_id> <page_no> [<page_no> ...] [--apply]
    python scripts/repair_kb_page_from_cache.py <book_id> --all [--apply]
"""

from __future__ import annotations

import sys

from app.kb import assets as kb_assets
from app.kb.markdown import blocks_to_page_markdown, group_blocks_by_page
from app.kb.mid import centered_texts_from_blocks, tag_source, write_mid
from app.kb.mineru_client import _rewrite_block
from app.kb.toc import extract_toc
from app.kb.workspace_store import mineru_dir, page_path, read_meta, write_page


def load_blocks(book_id: str) -> list[dict]:
    import json

    path = mineru_dir(book_id) / "blocks.json"
    if not path.is_file():
        raise SystemExit(f"no cached blocks: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"unexpected blocks.json shape: {type(data).__name__}")
    return data


def image_url_map(book_id: str, blocks: list[dict]) -> dict[str, str]:
    """Rebuild the `images/<name>` -> proxy-URL map that `_upload_figures` produced.

    `blocks.json` 是在上传插图**之前**落盘的（mineru_client.py:164-173），所以缓存里的
    图路径还是本地 `images/xxx.jpg`。插图本身早已在 MinIO，键名是确定的，照同一规则
    重建映射即可，不用重新上传。
    """
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key in ("img_path", "image_path", "src", "path") and isinstance(val, str):
                    text = val.replace("\\", "/")
                    if text.startswith("images/"):
                        names.add(text.split("/")[-1])
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blocks)
    return {
        f"images/{name}": kb_assets.asset_url(f"kb/pdfs/{book_id}/assets/mineru/{name}")
        for name in sorted(names)
    }


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    apply = "--apply" in argv
    want_all = "--all" in argv
    if len(args) < 1 or (not want_all and len(args) < 2):
        print(__doc__)
        return 2
    book_id = args[0]
    pages = [int(a) for a in args[1:]]

    blocks = load_blocks(book_id)
    url_map = image_url_map(book_id, blocks)
    if url_map:
        blocks = [_rewrite_block(b, url_map) for b in blocks]
        print(f"插图映射 {len(url_map)} 张（复用 MinIO 里已有的资源）")
    grouped = group_blocks_by_page(blocks)
    if want_all:
        meta = read_meta(book_id) or {}
        try:
            total = int(meta.get("page_count") or 0)
        except (TypeError, ValueError):
            total = 0
        if total < 1:
            total = max(grouped.keys(), default=0)
        pages = list(range(1, total + 1))
        print(f"全书重渲 {len(pages)} 页")
    # 与 process_kb_book 一致：印张页映射来自块本身，而不是 toc.json（那已经被后续工具改过）
    toc = extract_toc(blocks)
    mapping = toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else None

    for page_no in pages:
        rows = grouped.get(page_no, [])
        fresh = blocks_to_page_markdown(rows, print_to_pdf=mapping, book_id=book_id)
        current = ""
        path = page_path(book_id, page_no)
        if path.is_file():
            current = path.read_text(encoding="utf-8")
        print(f"=== page {page_no}: blocks={len(rows)} 现有 {len(current.splitlines())} 行 -> 重渲 {len(fresh.splitlines())} 行")
        if not rows:
            print("  缓存里没有这一页的块，跳过")
            continue
        if not apply:
            print("  --- 重渲结果预览 ---")
            for line in fresh.splitlines():
                print("  " + line)
            continue
        write_page(book_id, page_no, fresh)
        write_mid(book_id, page_no, tag_source(fresh, center_texts=centered_texts_from_blocks(rows)))
        print("  已写回 pages/ 与 mid/")

    if not apply:
        print("\n未写盘。确认无误后加 --apply。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

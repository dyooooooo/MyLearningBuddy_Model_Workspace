"""V2 → v2md/pages + index. Isolated from pages/ and mid/."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from v2md_lib import (
    atomic_write,
    book_dir,
    load_v2_pages,
    page_frontmatter,
    print_page_of,
    read_meta,
    render_page_markdown,
    safe_book_id,
    strip_frontmatter,
)


def v2md_generate(book_id: str, page_no: int = 0, root: str = "") -> dict[str, Any]:
    """Read MinerU V2 JSON and write textbooks/<id>/v2md/ only."""
    sid = safe_book_id(book_id)
    book = book_dir(sid, root)
    if not book.is_dir():
        raise FileNotFoundError(str(book))
    pages = load_v2_pages(book)
    if not pages:
        raise ValueError("content_list_v2 has no pages")
    want = int(page_no or 0)
    if want < 0:
        raise ValueError("page_no must be >= 0")
    targets = [want] if want else sorted(pages)
    if want and want not in pages:
        raise FileNotFoundError(f"page {want} missing in content_list_v2")
    meta = read_meta(book)
    tags = ["textbook"]
    subject = str(meta.get("subject") or "").strip()
    if subject:
        tags.append(subject)
    page_count = max(pages) if pages else 0
    dest = book / "v2md" / "pages"
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for n in targets:
        text = render_page_markdown(
            pages[n],
            book_id=sid,
            page_no=n,
            page_count=page_count,
            print_page=print_page_of(book, n),
            tags=tags,
        )
        atomic_write(dest / f"{n:04d}.md", text)
        written += 1
    index_path = book / "v2md" / "index.md"
    book_md_path = book / "v2md" / "book.md"
    _write_index(index_path, sid, meta, sorted(pages))
    _write_book_export(book_md_path, dest, sorted(pages))
    return {
        "ok": True,
        "book_id": sid,
        "pages": page_count,
        "written": written,
        "index": str(index_path),
        "book": str(book_md_path),
    }


def _write_index(path: Path, book_id: str, meta: dict[str, Any], page_nos: list[int]) -> None:
    label = str(meta.get("label") or book_id).strip() or book_id
    tags = ["textbook"]
    subject = str(meta.get("subject") or "").strip()
    if subject:
        tags.append(subject)
    lines = [page_frontmatter(book_id=book_id, page_no=None, print_page=None, tags=tags), "", f"# {label}", ""]
    for n in page_nos:
        lines.append(f"- [[{n:04d}|第 {n} 页]]")
    lines.append("")
    atomic_write(path, "\n".join(lines))


def _write_book_export(path: Path, pages_dir: Path, page_nos: list[int]) -> None:
    chunks: list[str] = []
    for n in page_nos:
        src = pages_dir / f"{n:04d}.md"
        if not src.is_file():
            continue
        body = strip_frontmatter(src.read_text(encoding="utf-8")).strip()
        if body:
            chunks.append(body)
    atomic_write(path, "\n\n---\n\n".join(chunks).rstrip() + ("\n" if chunks else ""))

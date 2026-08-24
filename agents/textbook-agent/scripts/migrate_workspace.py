#!/usr/bin/env python3
"""Copy current Agent files + optional Postgres/MinIO books into data/agent-home/."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOME = ROOT / "data" / "agent-home"
SEED_CURSOR = ROOT / "agents" / "textbook-agent" / "workspace" / ".cursor"
SEED_TOOLS = ROOT / "agents" / "textbook-agent" / "workspace" / "tools"
OLD_LOGS = ROOT / "data" / "agent-logs" / "runs.jsonl"
OLD_BOOKS = ROOT / "data" / "textbooks"


def _copy_tree(src: Path, dest: Path) -> None:
    if not src.exists():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        target = dest / item.relative_to(src)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _split_jsonl() -> int:
    traces = HOME / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    if not OLD_LOGS.is_file():
        return 0
    n = 0
    for line in OLD_LOGS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        job_id = str(item.get("job_id") or "").strip()
        if not job_id:
            continue
        dest = traces / f"{job_id}.json"
        if dest.exists():
            continue
        dest.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n += 1
    return n


def _export_postgres() -> list[dict]:
    cmd = [
        "docker",
        "exec",
        "lb-postgres",
        "psql",
        "-U",
        "buddy",
        "-d",
        "buddy",
        "-At",
        "-c",
        "SELECT coalesce(json_agg(row_to_json(t)), '[]'::json) FROM kb_books t;",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return []
    try:
        data = json.loads(out.strip() or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _minio_get(key: str) -> bytes:
    cmd = [
        "docker",
        "exec",
        "lb-minio",
        "mc",
        "cat",
        f"local/companion-frames/{key}",
    ]
    try:
        return subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return b""


def _export_pages(book_id: str) -> list[dict]:
    sql = (
        "SELECT coalesce(json_agg(row_to_json(t) ORDER BY page_no), '[]'::json) "
        f"FROM kb_pages t WHERE book_id = '{book_id}';"
    )
    cmd = ["docker", "exec", "lb-postgres", "psql", "-U", "buddy", "-d", "buddy", "-At", "-c", sql]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
        data = json.loads(out.strip() or "[]")
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _export_books() -> int:
    books = _export_postgres()
    n = 0
    for book in books:
        if not isinstance(book, dict):
            continue
        book_id = str(book.get("id") or "").strip()
        if not book_id:
            continue
        folder = HOME / "textbooks" / book_id
        if (folder / "meta.json").exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        key = str(book.get("minio_key") or "")
        pdf = _minio_get(key) if key else b""
        if pdf:
            (folder / "book.pdf").write_bytes(pdf)
        toc = {}
        try:
            parsed = json.loads(book.get("toc_json") or "{}")
            if isinstance(parsed, dict):
                toc = parsed
        except json.JSONDecodeError:
            toc = {}
        (folder / "toc.json").write_text(
            json.dumps(
                {
                    "entries": toc.get("entries") or [],
                    "print_to_pdf": toc.get("print_to_pdf") or {},
                    "toc_pages": toc.get("toc_pages") or [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        meta = {
            "id": book_id,
            "label": book.get("label") or "",
            "filename": book.get("filename") or "book.pdf",
            "subject": book.get("subject") or "math",
            "grade_band": book.get("grade_band") or "hs",
            "textbook_hint": book.get("textbook_hint") or book.get("label") or "",
            "status": book.get("status") or "uploaded",
            "page_count": int(book.get("page_count") or 0),
            "error": book.get("error") or "",
            "is_default": bool(book.get("is_default")),
            "created_at": book.get("created_at") or "",
            "updated_at": book.get("updated_at") or "",
        }
        (folder / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for page in _export_pages(book_id):
            if not isinstance(page, dict):
                continue
            try:
                page_no = int(page.get("page_no") or 0)
            except (TypeError, ValueError):
                continue
            if page_no < 1:
                continue
            md = folder / "pages" / f"{page_no:04d}.md"
            md.parent.mkdir(parents=True, exist_ok=True)
            md.write_text(str(page.get("markdown") or ""), encoding="utf-8")
            img_key = str(page.get("image_key") or "")
            if img_key:
                blob = _minio_get(img_key)
                if blob:
                    img = folder / "images" / f"p{page_no:04d}.jpg"
                    img.parent.mkdir(parents=True, exist_ok=True)
                    img.write_bytes(blob)
        n += 1
    return n


def _copy_loose_textbooks() -> int:
    if not OLD_BOOKS.is_dir():
        return 0
    dest = HOME / "textbooks" / "legacy"
    n = 0
    for path in list(OLD_BOOKS.rglob("*.pdf")) + list(OLD_BOOKS.rglob("*.md")):
        if path.name == "SKILL.md":
            continue
        target = dest / path.relative_to(OLD_BOOKS)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        n += 1
    return n


def main() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "textbooks").mkdir(parents=True, exist_ok=True)
    (HOME / "traces").mkdir(parents=True, exist_ok=True)
    _copy_tree(SEED_CURSOR, HOME / ".cursor")
    _copy_tree(SEED_TOOLS, HOME / "tools")
    traces = _split_jsonl()
    books = _export_books()
    loose = _copy_loose_textbooks()
    print(f"agent-home={HOME}")
    print(f"traces={traces} books={books} loose_files={loose}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetch MinerU Cloud official Markdown into textbooks/<id>/mineru/full.md.

Does not rewrite pages/, images/, or toc. Skips books that already have full.md.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "learning-buddy-api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from app.kb.mineru_client import parse_pdf, persist_official_markdown  # noqa: E402
from app.kb.settings import require_export_config  # noqa: E402
from app.kb.workspace_store import mineru_dir, pdf_path, read_meta  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("restore-md")

TEXTBOOKS = Path(
    os.environ.get(
        "AGENT_TEXTBOOKS_DIR",
        str(ROOT / "data/agent-home/textbooks"),
    )
)
MIN_PDF_BYTES = 20_000
MIN_MD_BYTES = 80


def _books() -> list[str]:
    rows: list[tuple[int, str]] = []
    for folder in sorted(p for p in TEXTBOOKS.iterdir() if p.is_dir()):
        meta = read_meta(folder.name)
        if not meta:
            continue
        path = pdf_path(folder.name)
        if not path.is_file():
            log.warning("skip %s: no pdf", folder.name)
            continue
        size = path.stat().st_size
        if size < MIN_PDF_BYTES:
            log.warning("skip %s: pdf too small (%s bytes)", folder.name, size)
            continue
        rows.append((size, folder.name))
    rows.sort()
    return [name for _size, name in rows]


def _has_official_md(book_id: str) -> bool:
    dest = mineru_dir(book_id)
    target = dest / "full.md"
    if target.is_file() and target.stat().st_size >= MIN_MD_BYTES:
        return True
    if not dest.is_dir():
        return False
    for path in dest.glob("*.md"):
        if path.stat().st_size >= MIN_MD_BYTES:
            return True
    return False


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _install_via_docker(book_id: str, src_dir: Path) -> None:
    container = os.environ.get("BUDDY_API_CONTAINER", "buddy-api")
    dest = f"/data/agent/textbooks/{book_id}/mineru"
    subprocess.run(
        ["docker", "exec", container, "mkdir", "-p", dest],
        check=True,
    )
    for path in sorted(src_dir.glob("*.md")):
        subprocess.run(
            ["docker", "cp", str(path), f"{container}:{dest}/{path.name}"],
            check=True,
        )


def restore_one(book_id: str) -> dict:
    if _has_official_md(book_id):
        return {"book_id": book_id, "status": "exists"}
    meta = read_meta(book_id) or {}
    raw = pdf_path(book_id).read_bytes()
    cfg = replace(require_export_config(), timeout_s=3600.0)
    tmp = Path(tempfile.mkdtemp(prefix=f"kb-md-{book_id[:8]}-"))
    try:
        parsed = parse_pdf(
            raw,
            filename=str(meta.get("filename") or "book.pdf"),
            book_id=book_id,
            cfg=cfg,
            persist_dir=tmp,
            upload_figures=False,
        )
        dest = mineru_dir(book_id)
        name = persist_official_markdown(tmp, tmp, parsed.markdown)
        if _writable(dest):
            persist_official_markdown(tmp, dest, parsed.markdown)
        else:
            _install_via_docker(book_id, tmp)
        target = dest / "full.md"
        if not target.is_file() or target.stat().st_size < MIN_MD_BYTES:
            raise RuntimeError("official markdown missing or empty after Cloud fetch")
        return {
            "book_id": book_id,
            "status": "ok",
            "bytes": target.stat().st_size,
            "file": name,
            "label": str(meta.get("label") or ""),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    books = wanted or _books()
    log.info("queue %s books under %s", len(books), TEXTBOOKS)
    results: list[dict] = []
    failed = 0
    for i, book_id in enumerate(books, 1):
        log.info("[%s/%s] %s", i, len(books), book_id)
        try:
            row = restore_one(book_id)
        except Exception as exc:
            failed += 1
            row = {"book_id": book_id, "status": "error", "error": str(exc)}
            log.exception("failed %s", book_id)
        results.append(row)
        log.info("result %s", json.dumps(row, ensure_ascii=False))
        live = TEXTBOOKS.parent / "mineru-official-md-restore.json"
        live.write_text(
            json.dumps(
                {
                    "total": len(books),
                    "done": len(results),
                    "ok": sum(1 for r in results if r.get("status") == "ok"),
                    "exists": sum(1 for r in results if r.get("status") == "exists"),
                    "error": failed,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.get("status") == "ok"),
        "exists": sum(1 for r in results if r.get("status") == "exists"),
        "error": failed,
        "results": results,
    }
    out = TEXTBOOKS.parent / "mineru-official-md-restore.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log.info("wrote %s ok=%s exists=%s error=%s", out, summary["ok"], summary["exists"], failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

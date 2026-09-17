#!/usr/bin/env python3
"""Write official MinerU figures into textbooks/<id>/mineru/images/.

Prefers objects already in MinIO (same filename as full.md). Books still
missing figures are listed so a Cloud zip can fill them.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(
    os.environ.get(
        "AGENT_TEXTBOOKS_DIR",
        "/data/agent/textbooks",
    )
)

API = Path(__file__).resolve().parents[1] / "learning-buddy-api"
if API.is_dir() and str(API) not in sys.path:
    sys.path.insert(0, str(API))

from app.kb.assets import read_bytes  # noqa: E402

IMG = re.compile(r"""(?:!\[[^\]]*\]\(|(?:src|href)=["'])(images/([^"')]+))""")


def _names(md: Path) -> set[str]:
    text = md.read_text(encoding="utf-8", errors="replace")
    return {m.group(2) for m in IMG.finditer(text)}


def restore_from_minio(book_id: str) -> dict:
    dest_root = ROOT / book_id / "mineru"
    md = dest_root / "full.md"
    if not md.is_file():
        return {"book_id": book_id, "status": "no-md"}
    names = _names(md)
    if not names:
        return {"book_id": book_id, "status": "no-refs"}
    dest = dest_root / "images"
    dest.mkdir(parents=True, exist_ok=True)
    ok = 0
    missing: list[str] = []
    for name in sorted(names):
        target = dest / name
        if target.is_file() and target.stat().st_size > 32:
            ok += 1
            continue
        data = read_bytes(f"kb/pdfs/{book_id}/assets/mineru/{name}")
        if not data:
            missing.append(name)
            continue
        target.write_bytes(data)
        ok += 1
    status = "ok" if not missing else ("partial" if ok else "missing")
    return {
        "book_id": book_id,
        "status": status,
        "ok": ok,
        "need": len(names),
        "missing": len(missing),
    }


def main() -> int:
    wanted = [a for a in sys.argv[1:] if not a.startswith("-")]
    books = wanted or [
        p.name
        for p in sorted(ROOT.iterdir())
        if p.is_dir() and (p / "mineru" / "full.md").is_file()
    ]
    results = [restore_from_minio(book_id) for book_id in books]
    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.get("status") == "ok"),
        "partial": sum(1 for r in results if r.get("status") == "partial"),
        "missing": sum(1 for r in results if r.get("status") == "missing"),
        "no_refs": sum(1 for r in results if r.get("status") == "no-refs"),
        "results": results,
    }
    out = ROOT.parent / "mineru-official-images-restore.json"
    try:
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        Path("/tmp/mineru-official-images-restore.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({k: summary[k] for k in summary if k != "results"}, ensure_ascii=False))
    return 0 if summary["missing"] == 0 and summary["partial"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

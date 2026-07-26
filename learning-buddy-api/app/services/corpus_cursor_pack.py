"""Assemble a Cursor Repair Pack from corpus (MinerU) feedback + debug artifacts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.config import settings
from app.db.models import CorpusImportFeedback
from app.services import corpus_feedback_store as store
from app.util.json_util import loads

logger = logging.getLogger(__name__)

_EVIDENCE_FILES = {
    "page_blocks.json": "page_blocks.json",
    "page.html": "page.html",
    "debug.json": "debug.json",
    "mineru_page.json": "mineru_page.json",
    "page.jpg": "page.jpg",
}


def _pack_root(feedback_id: str) -> Path:
    base = Path(settings.corpus_cursor_pack_dir or "/data/buddy/cursor-packs-corpus")
    return base / feedback_id


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    _write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _triage(t1: list[dict], body: str, tags: list[str]) -> dict[str, Any]:
    """Lightweight triage for corpus packs (no import_35b dependency)."""
    types = {str(f.get("suspect_type") or "") for f in t1 if isinstance(f, dict)}
    tagset = {str(t).lower() for t in tags}
    body_l = (body or "").lower()
    if "blank_page" in tagset or "PAGE_EMPTY" in types or "空白" in body:
        layer = "blank"
    elif "MATH_FALLBACK" in types or "math" in tagset or "公式" in body:
        layer = "math"
    elif "MAIN_EMPTY" in types or "layout" in tagset or "排版" in body:
        layer = "layout"
    else:
        layer = "content"
    return {
        "layer": layer,
        "suspect_types": sorted(t for t in types if t),
        "summary_zh": f"corpus/{layer} · findings={len(t1)} · tags={','.join(tags) or '-'}",
    }


def build_cursor_pack(session: Session, feedback: CorpusImportFeedback) -> dict[str, Any]:
    debug_prefix = feedback.debug_prefix
    if not debug_prefix:
        raise ValueError("feedback missing debug_prefix")

    tags = loads(feedback.tags_json, [])
    t1 = loads(feedback.t1_snapshot_json, [])
    triage = _triage(t1 if isinstance(t1, list) else [], feedback.body, tags if isinstance(tags, list) else [])

    root = _pack_root(feedback.id)
    if root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                try:
                    p.rmdir()
                except OSError:
                    pass
    evidence_dir = root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in _EVIDENCE_FILES:
        data = get_object_bytes(f"{debug_prefix.rstrip('/')}/{name}")
        if not data:
            continue
        _write_bytes(evidence_dir / name, data)
        copied.append(name)

    if feedback.screenshot_key:
        shot = get_object_bytes(feedback.screenshot_key)
        if shot:
            _write_bytes(evidence_dir / "screenshot.jpg", shot)
            copied.append("screenshot.jpg")

    _write_json(
        root / "feedback.json",
        store.feedback_to_dict(feedback),
    )
    _write_json(root / "triage.json", triage)
    _write_json(
        root / "golden.stub.json",
        {
            "case_id": f"corpus_fb_{feedback.id[:8]}_p{feedback.page_no}",
            "page_no": feedback.page_no,
            "feedback_id": feedback.id,
            "job_id": feedback.job_id,
            "layer": triage["layer"],
            "line": "mineru_corpus",
            "note": feedback.body[:200],
            "expect": {"t1_hard_findings": []},
        },
    )
    _write_text(
        root / "README.md",
        "\n".join(
            [
                f"# Corpus Cursor Pack · {feedback.id}",
                "",
                f"- job: `{feedback.job_id}` page `{feedback.page_no}`",
                f"- layer: `{triage['layer']}`",
                f"- created: {datetime.now(timezone.utc).isoformat()}",
                "",
                "## User note",
                "",
                feedback.body or "(empty)",
                "",
                "## Evidence",
                "",
                *([f"- `{n}`" for n in copied] if copied else ["- (none)"]),
                "",
            ]
        ),
    )
    _write_text(
        root / "EXEC.plan.md",
        "\n".join(
            [
                f"# EXEC · corpus {triage['layer']} p{feedback.page_no}",
                "",
                "1. Read `feedback.json` + `triage.json` + `evidence/debug.json`",
                "2. Fix MinerU IR adapter / render / blank — **not** 35B blueprint fill",
                "3. Reprocess page via corpus `rerender` and confirm T1 clears",
                "",
            ]
        ),
    )

    pack_path = str(root)
    store.set_feedback_status(session, feedback, pack_path=pack_path)
    logger.info(
        "corpus cursor pack built id=%s path=%s files=%d",
        feedback.id,
        pack_path,
        len(copied),
    )
    return {
        "ok": True,
        "feedback_id": feedback.id,
        "pack_path": pack_path,
        "triage": triage,
        "evidence_files": copied,
    }

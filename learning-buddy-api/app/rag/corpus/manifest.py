from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.rag.corpus.loaders import load_all_corpus


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def build_source_fingerprint(corpus_dir: Path) -> dict[str, str]:
    fp: dict[str, str] = {}
    for sub in ("syllabus", "question-bank"):
        d = corpus_dir / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                fp[str(p.relative_to(corpus_dir))] = _file_hash(p)
    return fp


def write_manifest(
    *,
    doc_count: int,
    syllabus_count: int,
    bank_count: int,
    corpus_dir: Path | None = None,
) -> dict:
    corpus_dir = corpus_dir or Path(settings.corpus_dir)
    manifest = {
        "embed_model": settings.embed_model,
        "collection": settings.qdrant_collection,
        "build_time": datetime.now(timezone.utc).isoformat(),
        "doc_count": doc_count,
        "syllabus_count": syllabus_count,
        "question_bank_count": bank_count,
        "sources": build_source_fingerprint(corpus_dir),
    }
    settings.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def read_manifest() -> dict | None:
    p = settings.manifest_path
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def manifest_stale(corpus_dir: Path | None = None) -> bool:
    corpus_dir = corpus_dir or Path(settings.corpus_dir)
    saved = read_manifest()
    if not saved:
        return True
    if saved.get("embed_model") != settings.embed_model:
        return True
    if saved.get("collection") != settings.qdrant_collection:
        return True
    return saved.get("sources") != build_source_fingerprint(corpus_dir)


def count_by_type(corpus_dir: Path | None = None) -> tuple[int, int, int]:
    docs = load_all_corpus(corpus_dir or Path(settings.corpus_dir))
    s = sum(1 for d in docs if d.doc_type == "syllabus")
    q = sum(1 for d in docs if d.doc_type == "question_bank")
    return len(docs), s, q

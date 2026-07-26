from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_session_factory
from app.rag.corpus.loaders import kp_labels_en_from_docs, kp_labels_from_docs, load_all_corpus, sync_docs_to_graph
from app.rag.corpus.manifest import count_by_type, manifest_stale, write_manifest
from app.rag.vector.factory import get_vector_store

logger = logging.getLogger(__name__)

_kp_labels: dict[str, str] = {}
_kp_labels_en: dict[str, str] = {}


def kp_label(kp_id: str) -> str:
    return _kp_labels.get(kp_id, kp_id)


def kp_label_en(kp_id: str) -> str:
    return _kp_labels_en.get(kp_id, kp_id)


def set_kp_labels(zh: dict[str, str], en: dict[str, str] | None = None) -> None:
    global _kp_labels, _kp_labels_en
    _kp_labels = zh
    _kp_labels_en = en or {}


def _load_kp_labels_from_disk() -> None:
    docs = load_all_corpus(Path(settings.corpus_dir))
    set_kp_labels(kp_labels_from_docs(docs), kp_labels_en_from_docs(docs))


def reseed_graph_from_files(session: Session, *, status: str = "draft") -> tuple[int, int, int]:
    """Load syllabus/question-bank files into PG graph only — no Milvus embed."""
    corpus_dir = Path(settings.corpus_dir)
    docs = load_all_corpus(corpus_dir)
    set_kp_labels(kp_labels_from_docs(docs), kp_labels_en_from_docs(docs))
    if not docs:
        logger.warning("No corpus documents under %s", corpus_dir)
        write_manifest(doc_count=0, syllabus_count=0, bank_count=0)
        return 0, 0, 0
    n = sync_docs_to_graph(session, docs, status=status)
    total, s, q = count_by_type()
    write_manifest(doc_count=total, syllabus_count=s, bank_count=q)
    logger.info("Graph reseeded as %s: nodes=%s syllabus=%s bank=%s", status, n, s, q)
    return n, s, q


def sync_graph_from_files(*, status: str = "draft") -> tuple[int, int, int]:
    """PG 图同步（不写入 Milvus）。新内容一律 draft，须经审核后 publish。"""
    factory = get_session_factory()
    with factory() as session:
        n, s, q = reseed_graph_from_files(session, status=status)
        session.commit()
    return n, s, q


def full_rebuild(force: bool = False) -> tuple[int, int]:
    """
    从 data/ 同步 PG 图（默认 draft）。不再自动 embed 进 Milvus。
    force=True 时全量重扫文件覆盖图节点（仍保持 draft）。
    """
    del force
    factory = get_session_factory()
    with factory() as session:
        n, s, q = reseed_graph_from_files(session, status="draft")
        session.commit()
    logger.info("Graph sync complete nodes=%s syllabus=%s bank=%s (draft only)", n, s, q)
    return s, q


def load_kp_labels_only() -> None:
    """Read-only: cache KP labels from data/ for display. Never writes PG or Milvus."""
    _load_kp_labels_from_disk()


def verify_and_index(force: bool = False) -> tuple[int, int]:
    """No-op for backward compat. KB is manual-only (Studio → review → publish)."""
    del force
    load_kp_labels_only()
    return 0, 0


def corpus_stats() -> tuple[int, int, bool]:
    try:
        store = get_vector_store()
        total = store.count()
        _, s, q = count_by_type()
        ok = not manifest_stale()
        return s, q, ok
    except Exception:
        return 0, 0, False

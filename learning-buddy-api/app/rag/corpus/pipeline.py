from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.db.session import get_session_factory
from app.rag.corpus.loaders import kp_labels_from_docs, load_all_corpus, sync_docs_to_graph
from app.rag.corpus.manifest import count_by_type, manifest_stale, write_manifest
from app.rag.embedder import embed_texts
from app.rag.vector.factory import get_vector_store

logger = logging.getLogger(__name__)

_kp_labels: dict[str, str] = {}


def kp_label(kp_id: str) -> str:
    return _kp_labels.get(kp_id, kp_id)


def full_rebuild(force: bool = False) -> tuple[int, int]:
    global _kp_labels
    corpus_dir = Path(settings.corpus_dir)
    docs = load_all_corpus(corpus_dir)
    _kp_labels = kp_labels_from_docs(docs)
    if not docs:
        logger.warning("No corpus documents found under %s", corpus_dir)
        return 0, 0

    texts = [d.embed_text for d in docs]
    vectors = embed_texts(texts)
    store = get_vector_store()
    if force:
        store.recreate_collection()
    store.upsert_documents(docs, vectors)

    factory = get_session_factory()
    with factory() as session:
        sync_docs_to_graph(session, docs)
        session.commit()

    total, s, q = count_by_type()
    write_manifest(doc_count=total, syllabus_count=s, bank_count=q)
    logger.info("Corpus indexed: total=%s syllabus=%s bank=%s", total, s, q)
    return s, q


def verify_and_index(force: bool = False) -> tuple[int, int]:
    if force or manifest_stale():
        return full_rebuild(force=True)
    total, s, q = count_by_type()
    store = get_vector_store()
    if store.count() == 0:
        return full_rebuild(force=True)
    global _kp_labels
    _kp_labels = kp_labels_from_docs(load_all_corpus(Path(settings.corpus_dir)))
    return s, q


def corpus_stats() -> tuple[int, int, bool]:
    try:
        store = get_vector_store()
        total = store.count()
        _, s, q = count_by_type()
        ok = total > 0 and not manifest_stale()
        return s, q, ok
    except Exception:
        return 0, 0, False

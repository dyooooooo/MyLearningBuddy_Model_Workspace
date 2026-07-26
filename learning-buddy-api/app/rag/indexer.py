"""Backward-compatible re-exports — use app.rag.corpus.pipeline instead."""

from app.rag.corpus.pipeline import corpus_stats, full_rebuild, kp_label, load_kp_labels_only, verify_and_index

index_corpus = verify_and_index

__all__ = [
    "index_corpus",
    "corpus_stats",
    "full_rebuild",
    "verify_and_index",
    "load_kp_labels_only",
    "kp_label",
]

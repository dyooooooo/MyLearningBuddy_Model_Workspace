#!/usr/bin/env python3
"""Index syllabus + question bank into Chroma."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.indexer import index_corpus

if __name__ == "__main__":
    s, q = index_corpus(force=True)
    print(f"Indexed syllabus={s} question_bank={q}")

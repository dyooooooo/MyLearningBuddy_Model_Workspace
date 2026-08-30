from __future__ import annotations


def toc_extract(book_id: str, root: str = "") -> dict:
    """先看原文 PDF 目录页，再读 toc.json，然后按兄弟节区间抽小节。对话里不另开 Cursor 会话。"""
    from app.toc_extract import run_book

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    try:
        from app.context import live_sdk_job

        job_id = live_sdk_job()
    except ImportError:
        from app.context import current_job_id

        job_id = current_job_id.get()
    return run_book(sid, job_id)

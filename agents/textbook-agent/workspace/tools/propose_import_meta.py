from __future__ import annotations

SUBJECTS = {
    "math": "math",
    "mathematics": "math",
    "数学": "math",
    "chinese": "chinese",
    "yuwen": "chinese",
    "语文": "chinese",
    "physics": "physics",
    "物理": "physics",
    "chemistry": "chemistry",
    "化学": "chemistry",
    "biology": "biology",
    "生物": "biology",
    "english": "english",
    "英语": "english",
}

STAGES = {
    "ms": "ms",
    "junior": "ms",
    "初中": "ms",
    "hs": "hs",
    "senior": "hs",
    "高中": "hs",
    "ps": "ps",
    "小学": "ps",
}


def _norm(raw: str, table: dict[str, str], fallback: str) -> str:
    text = (raw or "").strip()
    return table.get(text.lower()) or table.get(text) or fallback


def propose_import_meta(
    book_id: str,
    label: str,
    textbook_hint: str,
    subject: str,
    grade_band: str,
    category: str = "",
) -> dict:
    """把判断出的显示名、书名提示、科目、学段、类型写回导入表。若还是草稿，接着入队全书识别。"""
    from app import bus, library
    from app.volume_title import arabic_volume_digits

    sid = (book_id or "").strip()
    name = arabic_volume_digits((label or "").strip())
    hint = arabic_volume_digits((textbook_hint or name).strip())
    if hint.lower().endswith(".pdf"):
        hint = name or hint[: -4]
    sub = _norm(subject, SUBJECTS, "math")
    grade = _norm(grade_band, STAGES, "hs")
    kind = library.normalize_category(category) if (category or "").strip() else None
    if not name:
        raise ValueError("label required")
    shown = kind or "textbook"
    convert: dict = {}
    started = False
    if sid:
        row = library.get_book(sid)
        if row is None:
            raise ValueError(f"book not found: {sid}")
        patch: dict = {"label": name, "textbook_hint": hint, "subject": sub, "grade_band": grade}
        if kind is not None:
            patch["category"] = kind
        library.patch_book(sid, **patch)
        shown = kind or str(row.get("category") or "textbook")
        if str(row.get("status") or "") == "draft":
            library.patch_book(sid, status="uploaded")
            try:
                from mineru_convert import mineru_convert

                convert = mineru_convert(sid)
                started = True
            except Exception as exc:
                convert = {"queued": False, "error": str(exc)[:400]}
    out = {
        "book_id": sid,
        "label": name,
        "textbook_hint": hint,
        "subject": sub,
        "grade_band": grade,
        "category": shown,
        "started_convert": started,
        "convert": convert,
    }
    bus.publish("import_meta", **out)
    return out

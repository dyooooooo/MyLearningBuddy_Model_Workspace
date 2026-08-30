from __future__ import annotations

import re

CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

VOLUME_NUM = re.compile(r"(第\s*)?([一二三四五六七八九十两零〇百]+)(\s*)([册本卷])")


def cn_int(raw: str) -> int | None:
    token = (raw or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token == "百":
        return 100
    if token.startswith("十") and len(token) == 2:
        tail = CN_DIGIT.get(token[1])
        return 10 + tail if tail is not None else None
    if token.endswith("十") and len(token) == 2:
        head = CN_DIGIT.get(token[0])
        return head * 10 if head else None
    if "十" in token and len(token) == 3:
        head = CN_DIGIT.get(token[0])
        tail = CN_DIGIT.get(token[2])
        if head is not None and tail is not None:
            return head * 10 + tail
    return CN_DIGIT.get(token)


def arabic_volume_digits(text: str) -> str:
    """把标题里的「第一册」写成「第1册」，方便按标题排序。"""

    def repl(match: re.Match[str]) -> str:
        prefix, token, space, unit = match.groups()
        num = cn_int(token)
        if num is None:
            return match.group(0)
        head = "第" if prefix else ""
        return f"{head}{num}{space}{unit}"

    return VOLUME_NUM.sub(repl, text or "")

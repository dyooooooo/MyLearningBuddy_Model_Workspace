from __future__ import annotations

import re

CJK_SPLIT = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff，。；：、！？（）《》""''…—]+)"
)

CJK_ONLY = re.compile(
    r"^[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff，。；：、！？（）《》""''…—]+$"
)

MATH_SIGNAL = re.compile(
    r"[√∞∪∩≤≥≠παβγθλμσφω×±∁→∣\\]"
    r"|[\u2080-\u2089]"
    r"|(?:\b[fg]\s*\()"
    r"|(?:[a-zA-Z]\s*[=<>])"
    r"|(?:\d+\s*/\s*[x\d(])"
    r"|(?:\([^)]*[+\-*/^][^)]*\))"
)


def split_cjk_parts(text: str) -> list[str]:
    if not text:
        return []
    return [p for p in CJK_SPLIT.split(text) if p != ""]


def is_cjk_run(part: str) -> bool:
    return bool(CJK_ONLY.match(part))


def looks_like_math(part: str) -> bool:
    return bool(MATH_SIGNAL.search(part))

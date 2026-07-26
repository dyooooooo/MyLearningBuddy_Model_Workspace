from __future__ import annotations

import re
import unicodedata

# Residual glyph fixes after PyMuPDF (沪教版 PDF font mapping)
_GLYPH_FIXES: tuple[tuple[str, str], ...] = (
    ("狓", "x"),
    ("犪", "a"),
    ("犫", "b"),
    ("犮", "d"),
    ("犲", "e"),
    ("犳", "f"),
    ("犵", "g"),
    ("犺", "h"),
    ("犻", "i"),
    ("犼", "j"),
    ("犽", "k"),
    ("犾", "l"),
    ("犿", "m"),
    ("狀", "n"),
    ("狁", "o"),
    ("狆", "p"),
    ("狇", "q"),
    ("狉", "r"),
    ("狊", "s"),
    ("狋", "t"),
    ("狌", "u"),
    ("狍", "v"),
    ("狎", "w"),
    ("狏", "y"),
    ("狑", "z"),
    ("犖", "N"),
    ("犣", "Z"),
    ("犙", "Q"),
    ("犚", "R"),
    ("槡", "√"),
    # PyMuPDF 沪教版常见集合/逻辑字母映射
    ("犃", "A"),
    ("犅", "B"),
    ("犆", "C"),
    ("犇", "D"),
    ("犈", "E"),
    ("犉", "F"),
    ("犌", "I"),
    ("犍", "J"),
    ("犎", "K"),
    ("犏", "L"),
    ("犐", "M"),
    ("犑", "N"),
    ("犓", "P"),
    ("犔", "Q"),
    ("犕", "R"),
    ("犖", "N"),
    ("犗", "O"),
    ("犘", "P"),
    ("犙", "Q"),
    ("犚", "R"),
    ("犛", "S"),
    ("犜", "T"),
    ("犝", "U"),
    ("犞", "V"),
    ("犟", "W"),
    ("犠", "X"),
    ("犡", "Y"),
    ("犢", "Z"),
    ("犱", "d"),
    ("狔", "y"),
    ("ｌｏｇ", "log"),
    ("＝", "="),
)

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_NOISE_LINE = re.compile(r"^[\s书定版印次开本字数定价易文网SHUXUEA-Za-z]{0,3}$")


def _is_vertical_noise_line(line: str) -> bool:
    s = line.strip()
    if len(s) != 1:
        return False
    if s in "·—－-_=+*":
        return False
    return bool(re.match(r"[\u4e00-\u9fffA-Za-z0-9]", s))


def normalize_pdf_text(text: str) -> str:
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_FULLWIDTH_DIGITS)
    for old, new in _GLYPH_FIXES:
        out = out.replace(old, new)

    lines: list[str] = []
    for line in out.splitlines():
        if _is_vertical_noise_line(line):
            continue
        if _NOISE_LINE.match(line.strip()) and len(line.strip()) <= 3:
            continue
        lines.append(line.rstrip())

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

from __future__ import annotations

import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_URLISH = re.compile(
    r"(https?://\S+|/api/v1/\S+|!\[[^\]]*\]\([^)]+\)|\[[^\]]*\]\([^)]+\))"
)
_PHONETIC = re.compile(r"/[^/\n]{1,48}/")
_LATIN_RUN = re.compile(r"[\x22-\x5A]{4,}")
_PAGE_REF = re.compile(r"^P\d+$")
_VOWEL = re.compile(r"[AEIOUaeiou]")


def _load_han() -> tuple[str, frozenset[str]]:
    for path in (
        Path(__file__).resolve().with_name("han_standard.txt"),
        Path("/data/agent/memory/han_standard.txt"),
    ):
        if path.is_file():
            text = "".join(path.read_text(encoding="utf-8").split())
            if len(text) >= 3000:
                return text, frozenset(text)
    return "", frozenset()


_HAN_ORDER, _HAN = _load_han()
_LEVEL1 = frozenset(_HAN_ORDER[:3500]) if len(_HAN_ORDER) >= 3500 else _HAN


def cjk_from_identity_cid(cp: int) -> int | None:
    """Complete an unmapped Identity CID for CJK (Lanting-style formula)."""
    cand = cp + 0x30F2
    if 0x4E00 <= cand <= 0x4EFF:
        cand = cp + 0x30F3
    if 0x4E00 <= cand <= 0x9FFF:
        return cand
    return None


def _protected_range(cp: int) -> bool:
    if cp <= 0x00FF:
        return True
    if 0x0370 <= cp <= 0x03FF:
        return True
    if 0x2000 <= cp <= 0x206F:
        return True
    if 0x20A0 <= cp <= 0x20CF:
        return True
    if 0x2460 <= cp <= 0x24FF:
        return True
    if 0x3000 <= cp <= 0x303F:
        return True
    if 0xFF00 <= cp <= 0xFFEF:
        return True
    return False


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return 0x2F00 <= cp <= 0x2FDF or 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF


def _near_cjk(text: str, i: int) -> bool:
    n = len(text)
    for j in (i - 1, i + 1):
        if 0 <= j < n and _is_cjk(text[j]):
            return True
    return False


def _map_rare_han(ch: str) -> str | None:
    """一级字不动；表外或二三级源字落到一级字时按 CID 还原。"""
    if ch not in _HAN:
        return None
    got = cjk_from_identity_cid(ord(ch))
    if got is None:
        return None
    tgt = chr(got)
    if tgt not in _LEVEL1 or ch in _LEVEL1:
        return None
    return tgt


def _map_cjk_char(ch: str, text: str, i: int) -> str | None:
    cp = ord(ch)
    rare = _map_rare_han(ch)
    if rare is not None:
        return rare
    if ch in _HAN or _protected_range(cp):
        return None
    got = cjk_from_identity_cid(cp)
    if got is None:
        return None
    tgt = chr(got)
    if tgt in _HAN:
        return tgt
    if not (0x2F00 <= cp <= 0x9FFF) and not _near_cjk(text, i):
        return None
    return tgt


def _latin_mapped(token: str) -> str | None:
    wrap = 0
    while len(token) > 2 and token[0] == "*" and token[-1] == "*":
        token = token[1:-1]
        wrap += 1
    if len(token) < 4 or _PAGE_REF.match(token) or token.isdigit():
        return None
    if any(ch in token for ch in "[]{}/\\"):
        return None
    out: list[str] = []
    letters = 0
    non_letters = 0
    for ch in token:
        nxt = ord(ch) + 31
        if not (0x41 <= nxt <= 0x5A or 0x61 <= nxt <= 0x7A):
            return None
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            letters += 1
        else:
            non_letters += 1
        out.append(chr(nxt))
    mapped = "".join(out)
    if letters >= 3 and non_letters <= letters and _VOWEL.search(
        mapped
    ):
        stars = "*" * wrap
        return f"{stars}{mapped}{stars}"
    return None


def _latin_context_ok(text: str, start: int, end: int) -> bool:
    left = text[start - 1] if start > 0 else ""
    right = text[end] if end < len(text) else ""
    if left and left in "abcdefghijklmnopqrstuvwxyz/$":
        return False
    if right and right in "abcdefghijklmnopqrstuvwxyz/$":
        return False
    return True


def restore_identity_text(
    text: str, apply_latin: bool = True
) -> tuple[str, int, int]:
    """Restore Identity CID leftovers. Returns (text, cjk_hits, latin_hits)."""
    if not text:
        return text, 0, 0
    parts = _URLISH.split(text)
    cjk_hits = 0
    latin_hits = 0
    rebuilt: list[str] = []
    for part in parts:
        if not part:
            continue
        if _URLISH.fullmatch(part):
            rebuilt.append(part)
            continue
        chars = list(part)
        for i, ch in enumerate(chars):
            mapped = _map_cjk_char(ch, part, i)
            if mapped is not None:
                chars[i] = mapped
                cjk_hits += 1
        chunk = "".join(chars)
        if not apply_latin:
            rebuilt.append(chunk)
            continue
        masked = [(m.start(), m.end()) for m in _PHONETIC.finditer(chunk)]

        def in_phonetic(pos: int) -> bool:
            return any(a <= pos < b for a, b in masked)

        def repl(m: re.Match[str]) -> str:
            nonlocal latin_hits
            if in_phonetic(m.start()) or not _latin_context_ok(
                chunk, m.start(), m.end()
            ):
                return m.group(0)
            mapped = _latin_mapped(m.group(0))
            if mapped is None:
                return m.group(0)
            latin_hits += len(mapped)
            return mapped

        rebuilt.append(_LATIN_RUN.sub(repl, chunk))
    return "".join(rebuilt), cjk_hits, latin_hits


def _page_paths(base: Path, book_id: str) -> list[Path]:
    if (base / "textbooks").is_dir():
        pages_dir = base / "textbooks" / book_id / "pages"
    else:
        pages_dir = base / book_id / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))
    return sorted(pages_dir.glob("*.md"))


def restore_cid(book_id: str, apply: str = "scan", root: str = "") -> dict[str, object]:
    """Scan or fix Identity CID leftovers in one textbook's Markdown pages."""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "scan").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")
    base = Path(root).resolve() if root else Path.cwd()
    paths = _page_paths(base, sid)

    originals: list[tuple[Path, str]] = []
    cjk_hits = 0
    for path in paths:
        original = path.read_text(encoding="utf-8")
        originals.append((path, original))
        _restored, cj, _lt = restore_identity_text(original, apply_latin=False)
        cjk_hits += cj

    use_latin = cjk_hits > 0
    pages_with_hits: list[str] = []
    latin_hits = 0
    changed_pages = 0
    samples: list[str] = []
    measured: list[tuple[Path, str, str]] = []

    for path, original in originals:
        restored, cj, lt = restore_identity_text(original, apply_latin=use_latin)
        measured.append((path, original, restored))
        if cj or lt:
            pages_with_hits.append(path.name)
            latin_hits += lt
            if len(samples) < 8:
                samples.append(f"{path.name}:{cj}+{lt}")

    result: dict[str, object] = {
        "book_id": sid,
        "apply": mode,
        "pages_with_hits": pages_with_hits,
        "cjk_hits": cjk_hits,
        "latin_hits": latin_hits,
        "hit_count": cjk_hits + latin_hits,
        "han_table": len(_HAN),
        "latin_enabled": use_latin,
        "samples": samples,
    }
    if mode != "fix":
        return result
    if cjk_hits + latin_hits == 0:
        result["changed_pages"] = 0
        result["skipped"] = "no identity cid residue"
        return result
    for path, original, restored in measured:
        if restored != original:
            try:
                from source_text import write_source_atomic
            except ImportError:
                write_source_atomic = None
            if write_source_atomic is not None:
                write_source_atomic(path, restored)
            else:
                tmp = path.with_suffix(".md.tmp")
                tmp.write_text(restored, encoding="utf-8")
                tmp.replace(path)
            changed_pages += 1
    result["changed_pages"] = changed_pages
    return result

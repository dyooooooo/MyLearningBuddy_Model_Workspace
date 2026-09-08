from __future__ import annotations

import re
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_ATOM = (
    r"[A-Za-z0-9:'æɑɒɔəɜɪʊʌʃʒθðŋɡːˈˌ]+"
    r"(?:\(\s*r\s*\))?"
    r"[A-Za-z0-9:'æɑɒɔəɜɪʊʌʃʒθðŋɡːˈˌ]*"
)
_SPAN = re.compile(rf"/{_ATOM}(?: {{1,2}}{_ATOM})?/")
_HTMLISH = re.compile(r"[<>]|/tr|/td")
_IPA_KEY = frozenset("90IRCWNSZBGVOAFPQ")
_URLISH = re.compile(
    r"(https?://\S+|/api/v1/\S+|!\[[^\]]*\]\([^)]+\)|\[[^\]]*\]\([^)]+\))"
)

# From the book's Phonetic symbols table (TimesKKPhoneticNewRoman / Times-PhoneticIPA).
_MAP = {
    "9": "\u02c8",
    "0": "\u02cc",
    "'": "\u02c8",
    ":": "\u02d0",
    "I": "\u026a",
    "R": "\u0259",
    "C": "\u00e6",
    "W": "\u028a",
    "N": "\u014b",
    "S": "\u0283",
    "Z": "\u0292",
    "B": "\u0252",
    "G": "\u0261",
    "V": "\u028c",
    "O": "\u0254",
    "A": "\u0251",
    "F": "\u025c",
    "P": "\u03b8",
    "Q": "\u00f0",
}


def _looks_phonetic(inner: str) -> bool:
    if _HTMLISH.search(inner):
        return False
    if not any(ch in _IPA_KEY for ch in inner):
        return False
    stripped = re.sub(r"\(\s*r\s*\)", "", inner)
    if "(" in stripped or ")" in stripped:
        return False
    return True


def _restore_span(span: str) -> str:
    inner = span[1:-1]
    if not _looks_phonetic(inner):
        return span
    return "/" + "".join(_MAP.get(ch, ch) for ch in inner) + "/"


def restore_ipa_text(text: str) -> tuple[str, int]:
    if not text:
        return text, 0
    hits = 0
    parts = _URLISH.split(text)
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if _URLISH.fullmatch(part):
            out.append(part)
            continue

        def repl(match: re.Match[str]) -> str:
            nonlocal hits
            mapped = _restore_span(match.group(0))
            if mapped != match.group(0):
                hits += 1
            return mapped

        out.append(_SPAN.sub(repl, part))
    return "".join(out), hits


def _page_paths(base: Path, book_id: str) -> list[Path]:
    if (base / "textbooks").is_dir():
        pages_dir = base / "textbooks" / book_id / "pages"
    else:
        pages_dir = base / book_id / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))
    return sorted(pages_dir.glob("*.md"))


def restore_ipa(book_id: str, apply: str = "scan", root: str = "") -> dict[str, object]:
    """Scan or fix TimesKK phonetic ASCII leftovers in one textbook's Markdown pages."""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    mode = (apply or "scan").strip().lower()
    if mode not in {"scan", "fix"}:
        raise ValueError("apply must be scan or fix")
    base = Path(root).resolve() if root else Path.cwd()
    paths = _page_paths(base, sid)

    pages_with_hits: list[str] = []
    samples: list[str] = []
    hit_count = 0
    changed_pages = 0
    measured: list[tuple[Path, str, str, int]] = []

    for path in paths:
        original = path.read_text(encoding="utf-8")
        restored, hits = restore_ipa_text(original)
        measured.append((path, original, restored, hits))
        if hits:
            pages_with_hits.append(path.name)
            hit_count += hits
            if len(samples) < 8:
                samples.append(f"{path.name}:{hits}")

    result: dict[str, object] = {
        "book_id": sid,
        "apply": mode,
        "pages_with_hits": pages_with_hits,
        "hit_count": hit_count,
        "samples": samples,
    }
    if mode != "fix":
        return result
    if hit_count == 0:
        result["changed_pages"] = 0
        result["skipped"] = "no phonetic ascii residue"
        return result
    for path, original, restored, hits in measured:
        if hits and restored != original:
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

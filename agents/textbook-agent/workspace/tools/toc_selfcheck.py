"""Workspace TOC shape checks. Library module — not listed in manifest.yaml."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_toc as bt  # noqa: E402
import toc_lexicon as lex  # noqa: E402
import write_toc_pages as wtp  # noqa: E402


def _cases() -> dict:
    raw = yaml.safe_load((HERE / "toc_cases.yaml").read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _fail(message: str) -> None:
    raise SystemExit(f"toc_selfcheck: {message}")


def main() -> None:
    lex.reset_for_tests()
    cases = _cases()

    for row in cases.get("classify") or []:
        line = str(row.get("line") or "")
        got = lex.parse_toc_line(line)
        if got.get("id") != row.get("id") or got.get("kind") != row.get("kind"):
            _fail(f"classify {line!r} -> id={got.get('id')} kind={got.get('kind')}")

    for row in cases.get("reject") or []:
        line = str(row.get("line") or "")
        got = lex.parse_toc_line(line)
        if got.get("id") in {"toc_simple", "named_cn", "toc_section"}:
            _fail(f"reject still classified: {line!r}")
        label, _page = lex.strip_page(line)
        if label and not lex.rejected_title(label) and got.get("kind"):
            _fail(f"reject missed: {line!r} label={label!r}")

    leftover_pages = [(1, HERE / "0001.md", ["目录"])]
    for row in cases.get("leftover") or []:
        leftover_pages[0][2].append(str(row.get("line") or ""))
    leftover_pages[0][2].append("第1章 某章标题 5")
    _pages, skeleton, _subs = bt._parse_catalog(leftover_pages, known_toc_pages=[1])
    leftover_labels = {
        str(item.get("label") or "")
        for item in skeleton
        if item.get("kind") == "named"
    }
    for row in cases.get("leftover") or []:
        label, _page = lex.strip_page(str(row.get("line") or ""))
        if label not in leftover_labels:
            _fail(f"leftover missing {label!r} in {sorted(leftover_labels)}")

    cont = cases.get("continuation") or {}
    if not bt._is_toc_continuation(list(cont.get("lines") or []), bool(cont.get("prev_toc"))):
        if cont.get("expect_toc"):
            _fail("continuation page should count as catalog")
    body = cases.get("appendix_only_body") or {}
    if bt._is_toc_continuation(list(body.get("lines") or []), bool(body.get("prev_toc"))):
        if not body.get("expect_toc"):
            _fail("appendix-head-only body page must not be a continuation")

    split = cases.get("split") or {}
    entries = list(split.get("entries") or [])
    texts = {int(k): str(v) for k, v in (split.get("pages") or {}).items()}
    chunks = wtp._split_entries(sorted(texts), entries, texts)
    if chunks is None:
        _fail("split_entries returned None")
    got = [str((chunk[0] or {}).get("label") or "") for chunk in chunks if chunk]
    want = [str(x) for x in (split.get("expect_first_labels") or [])]
    if got != want:
        _fail(f"split first labels {got} != {want}")

    print("toc_selfcheck: ok")


if __name__ == "__main__":
    main()

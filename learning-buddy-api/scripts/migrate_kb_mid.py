"""Strip baked kb-* HTML from imported pages and persist mid/ fences."""

from __future__ import annotations

import json
import sys

from app.kb.mid import migrate_all_books, migrate_book


def main() -> int:
    book_id = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    out = migrate_book(book_id) if book_id else migrate_all_books()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

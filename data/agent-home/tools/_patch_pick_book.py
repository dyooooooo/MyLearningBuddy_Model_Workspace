from pathlib import Path

p = Path("/tools/build_toc.py")
text = p.read_text(encoding="utf-8")
old = """    if book_id:
        direct = textbooks / book_id
        if (direct / \"pages\").is_dir():
            return direct
"""
new = """    if book_id:
        direct = textbooks / book_id
        return direct if direct.is_dir() else None
"""
if old not in text:
    raise SystemExit("pattern not found")
p.write_text(text.replace(old, new, 1), encoding="utf-8")
print("patched")

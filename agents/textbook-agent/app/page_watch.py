"""Watch textbooks pages/ and mid/ and run page_sync when either side changes."""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import settings
from app.page_sync import is_suppressed, sync_page

logger = logging.getLogger(__name__)

_REL = re.compile(r"^(?P<book>[A-Za-z0-9_-]+)/(?P<kind>pages|mid)/(?P<page>\d{4})\.md$")
_SKIP_TYPES = frozenset({"opened", "closed_no_write"})

_observer: Observer | None = None
_timers: dict[tuple[str, int], threading.Timer] = {}
_timer_lock = threading.Lock()
_scan_thread: threading.Thread | None = None


def reset_for_tests() -> None:
    stop()
    with _timer_lock:
        for timer in _timers.values():
            timer.cancel()
        _timers.clear()


def debounce_s() -> float:
    ms = int(getattr(settings, "page_sync_debounce_ms", 400) or 400)
    return max(ms, 0) / 1000.0


def parse_textbook_md(path: Path | str, root: Path | None = None) -> tuple[str, int] | None:
    raw = Path(path)
    base = (root or settings.textbooks_dir).resolve()
    try:
        rel = raw.resolve().relative_to(base).as_posix()
    except (OSError, ValueError):
        text = str(raw).replace("\\", "/")
        marker = "/textbooks/"
        idx = text.find(marker)
        if idx < 0:
            return None
        rel = text[idx + len(marker) :]
    match = _REL.match(rel)
    if not match:
        return None
    return match.group("book"), int(match.group("page"))


def _should_ignore(path: Path | str) -> bool:
    text = str(path).replace("\\", "/")
    if text.endswith(".tmp"):
        return True
    if "/.stamps/" in text:
        return True
    return False


def schedule(book_id: str, page_no: int) -> None:
    key = (book_id, int(page_no))
    delay = debounce_s()

    def _run() -> None:
        with _timer_lock:
            _timers.pop(key, None)
        try:
            from app.logsetup import bind, event, new_trace
            from app.page_sync import read_stamp

            stamp = read_stamp(book_id, page_no)
            prev = str(stamp.get("last_trace_id") or "")
            with bind(trace_id=new_trace(), book_id=book_id, origin="watch"):
                event(
                    "watch.fire",
                    book_id=book_id,
                    page_no=page_no,
                    prev_trace_id=prev,
                    prev_origin=str(stamp.get("last_origin") or ""),
                    fired_at_ms=int(time.time() * 1000),
                )
                sync_page(book_id, page_no)
        except Exception:
            logger.warning("page sync failed %s/%s", book_id, page_no, exc_info=True)

    with _timer_lock:
        old = _timers.pop(key, None)
        if old is not None:
            old.cancel()
        timer = threading.Timer(delay, _run)
        timer.daemon = True
        _timers[key] = timer
        timer.start()


def handle_path(path: Path | str, *, root: Path | None = None) -> None:
    if _should_ignore(path):
        return
    if is_suppressed(path):
        return
    parsed = parse_textbook_md(path, root)
    if parsed is None:
        return
    schedule(parsed[0], parsed[1])


class PagePairHandler(FileSystemEventHandler):
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if getattr(event, "event_type", "") in _SKIP_TYPES:
            return
        dest = getattr(event, "dest_path", "") or ""
        src = getattr(event, "src_path", "") or ""
        if dest:
            handle_path(dest)
        if src:
            handle_path(src)


def scan_all() -> int:
    from app import library

    root = library.textbooks_root()
    if not root.is_dir():
        return 0
    n = 0
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        pages = folder / "pages"
        if not pages.is_dir():
            continue
        for path in sorted(pages.glob("[0-9][0-9][0-9][0-9].md")):
            try:
                sync_page(folder.name, int(path.stem))
                n += 1
            except Exception:
                logger.warning("startup sync failed %s/%s", folder.name, path.stem, exc_info=True)
    return n


def start() -> None:
    global _observer, _scan_thread
    stop()
    root = settings.textbooks_dir
    root.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(PagePairHandler(), str(root), recursive=True)
    observer.start()
    _observer = observer
    _scan_thread = threading.Thread(target=_safe_scan, name="page-sync-scan", daemon=True)
    _scan_thread.start()
    logger.info("page pair watch started on %s", root)


def _safe_scan() -> None:
    try:
        n = scan_all()
        logger.info("page pair startup scan: %s page(s)", n)
    except Exception:
        logger.warning("page pair startup scan failed", exc_info=True)


def stop() -> None:
    global _observer
    with _timer_lock:
        for timer in _timers.values():
            timer.cancel()
        _timers.clear()
    observer = _observer
    _observer = None
    if observer is None:
        return
    try:
        observer.stop()
        observer.join(timeout=5)
    except Exception:
        logger.warning("page pair watch stop failed", exc_info=True)

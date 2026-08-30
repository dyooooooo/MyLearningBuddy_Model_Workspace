"""Walk a book's pages using the workspace refine recipe. One book at a time."""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import bus, jobs, library, prompts_store, sdk_runner
from app.tools_loader import load_tool_specs

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cursor_path(book_id: str) -> Path:
    return library.book_dir(book_id) / "refine.json"


def load_cursor(book_id: str) -> dict[str, Any]:
    path = cursor_path(book_id)
    if not path.is_file():
        return {"book_id": book_id, "next_page": 1, "sdk_id": "", "updated_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("book_id", book_id)
    data.setdefault("next_page", 1)
    data.setdefault("sdk_id", "")
    return data


def save_cursor(book_id: str, data: dict[str, Any]) -> None:
    path = cursor_path(book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["book_id"] = book_id
    payload["updated_at"] = _now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run_workspace_tool(book_id: str, page_no: int) -> dict[str, Any]:
    specs = {str(s.get("id") or ""): s for s in load_tool_specs()}
    spec = specs.get("refine_page")
    if spec is None:
        return {"skipped": True}
    result = spec["execute"]({"book_id": book_id, "page_no": page_no})
    return result if isinstance(result, dict) else {"result": result}


def _page_images(book_id: str, page_no: int) -> list[dict[str, Any]]:
    raw = library.get_page_image(book_id, page_no)
    if not raw:
        return []
    return [
        {
            "data": base64.b64encode(raw).decode("ascii"),
            "mime_type": "image/jpeg",
            "name": f"p{int(page_no):04d}.jpg",
        }
    ]


def run_book(book_id: str, job_id: str, prompt: str = "prompts/page_refine.md") -> dict[str, Any]:
    meta = library.get_book(book_id)
    if meta is None:
        raise ValueError(f"book not found: {book_id}")
    page_count = int(meta.get("page_count") or 0)
    if page_count < 1:
        return {"book_id": book_id, "pages_done": 0, "page_count": 0}
    cursor = load_cursor(book_id)
    start = max(1, int(cursor.get("next_page") or 1))
    sdk_id = str(cursor.get("sdk_id") or "")
    try:
        system = prompts_store.read_file(prompt or "prompts/page_refine.md")
    except (FileNotFoundError, ValueError):
        system = ""
    done = 0
    for page_no in range(start, page_count + 1):
        if job_id and jobs.is_cancelled(job_id):
            raise jobs.JobCancelled()
        jobs.append_trace(job_id, "tool", "page", f"清洗第 {page_no}/{page_count} 页")
        polished = _run_workspace_tool(book_id, page_no)
        md_rel = f"textbooks/{book_id}/pages/{int(page_no):04d}.md"
        body = (
            f"{system.rstrip()}\n\n"
            f"书：{book_id}\n"
            f"页：{page_no}/{page_count}\n"
            f"Markdown 路径：{md_rel}\n"
            "原图已附上。先用 refine_page 做确定性清洗，再对照原图改这一页，"
            "原子写回同一文件。不要把整页正文贴进对话。\n"
        )
        if isinstance(polished, dict) and polished.get("changed"):
            body += "确定性清洗已改过这一页，请再对原图核对。\n"
        text, sdk_id = sdk_runner._prompt_sdk(
            body,
            conversation_id="",
            resume_id=sdk_id,
            job_id=job_id,
            images=_page_images(book_id, page_no),
        )
        next_page = page_no + 1
        save_cursor(
            book_id,
            {
                "next_page": next_page,
                "page_count": page_count,
                "sdk_id": sdk_id,
                "last_reply": (text or "")[:200],
            },
        )
        jobs.patch_result(job_id, persist=True, page_no=page_no, next_page=next_page, sdk_id=sdk_id)
        bus.publish("page", book_id=book_id, page_no=page_no, job_id=job_id)
        done += 1
    save_cursor(
        book_id,
        {"next_page": page_count + 1, "page_count": page_count, "sdk_id": sdk_id, "done": True},
    )
    return {"book_id": book_id, "pages_done": done, "page_count": page_count, "sdk_id": sdk_id}

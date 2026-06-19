"""JSON volume persistence for companion sessions, homework, mastery (A/B/A+ demo)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import settings
from app.schemas import CompanionSession, HomeworkEntry, MasteryRecord, SessionEvent

ISO = lambda: datetime.now(timezone.utc).isoformat()


def _root() -> Path:
    root = Path(settings.companion_data_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(exist_ok=True)
    (root / "homework").mkdir(exist_ok=True)
    (root / "mastery").mkdir(exist_ok=True)
    return root


def _read_json(path: Path, default: dict | list) -> dict | list:
    if not path.is_file():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def new_session_id() -> str:
    return str(uuid.uuid4())


def new_page_id() -> str:
    return f"page-{uuid.uuid4().hex[:8]}"


def create_session(subject: str | None = None, note: str | None = None) -> CompanionSession:
    sid = new_session_id()
    now = ISO()
    session = CompanionSession(
        id=sid,
        status="active",
        started_at=now,
        current_page_id=new_page_id(),
        events=[SessionEvent(ts=now, type="SESSION_START", payload={"session_id": sid})],
        meta={"subject": subject, "note": note},
    )
    save_session(session)
    return session


def session_path(session_id: str) -> Path:
    return _root() / "sessions" / f"{session_id}.json"


def load_session(session_id: str) -> CompanionSession:
    path = session_path(session_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    data = _read_json(path, {})
    return CompanionSession.model_validate(data)


def save_session(session: CompanionSession) -> None:
    _write_json(session_path(session.id), session.model_dump(mode="json", by_alias=True))


def frame_preview_path(session_id: str, frame_id: str) -> Path:
    return _root() / "sessions" / session_id / "frames" / f"{frame_id}.jpg"


def save_frame_preview(session_id: str, frame_id: str, jpeg_bytes: bytes) -> None:
    path = frame_preview_path(session_id, frame_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg_bytes)


def read_frame_preview_bytes(session_id: str, frame_id: str) -> bytes | None:
    path = frame_preview_path(session_id, frame_id)
    if path.is_file():
        return path.read_bytes()
    return None


def load_frame_preview(session_id: str, frame_id: str) -> bytes:
    session = load_session(session_id)
    if not any(f.id == frame_id for f in session.frames):
        raise HTTPException(status_code=404, detail=f"Frame not found: {frame_id}")
    data = read_frame_preview_bytes(session_id, frame_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Frame preview not found: {frame_id}")
    return data


def append_event(session: CompanionSession, event_type: str, payload: dict | None = None) -> None:
    session.events.append(
        SessionEvent(ts=ISO(), type=event_type, payload=payload or {})
    )


def homework_index_path() -> Path:
    return _root() / "homework" / "index.json"


def homework_entry_path(entry_id: str) -> Path:
    return _root() / "homework" / f"{entry_id}.json"


def save_homework(entry: HomeworkEntry) -> HomeworkEntry:
    _write_json(
        homework_entry_path(entry.id),
        entry.model_dump(mode="json", by_alias=True),
    )
    idx = _read_json(homework_index_path(), {"ids": []})
    ids: list[str] = idx.get("ids", [])
    if entry.id not in ids:
        ids.append(entry.id)
        _write_json(homework_index_path(), {"ids": ids})
    return entry


def list_homework() -> list[HomeworkEntry]:
    idx = _read_json(homework_index_path(), {"ids": []})
    out: list[HomeworkEntry] = []
    for eid in idx.get("ids", []):
        data = _read_json(homework_entry_path(eid), {})
        if data:
            out.append(HomeworkEntry.model_validate(data))
    out.sort(key=lambda e: e.finalized_at, reverse=True)
    return out


def get_homework(entry_id: str) -> HomeworkEntry:
    data = _read_json(homework_entry_path(entry_id), {})
    if not data:
        raise HTTPException(status_code=404, detail=f"Homework not found: {entry_id}")
    return HomeworkEntry.model_validate(data)


def mastery_index_path() -> Path:
    return _root() / "mastery" / "index.json"


def mastery_record_path(kp_id: str) -> Path:
    safe = kp_id.replace("/", "_")
    return _root() / "mastery" / f"{safe}.json"


def list_mastery() -> list[MasteryRecord]:
    idx = _read_json(mastery_index_path(), {"ids": []})
    out: list[MasteryRecord] = []
    for kid in idx.get("ids", []):
        data = _read_json(mastery_record_path(kid), {})
        if data:
            out.append(MasteryRecord.model_validate(data))
    out.sort(key=lambda r: r.updated_at, reverse=True)
    return out


def upsert_mastery(record: MasteryRecord) -> MasteryRecord:
    _write_json(
        mastery_record_path(record.kp_id),
        record.model_dump(mode="json", by_alias=True),
    )
    idx = _read_json(mastery_index_path(), {"ids": []})
    ids: list[str] = idx.get("ids", [])
    if record.kp_id not in ids:
        ids.append(record.kp_id)
        _write_json(mastery_index_path(), {"ids": ids})
    return record


def get_mastery(kp_id: str) -> MasteryRecord:
    data = _read_json(mastery_record_path(kp_id), {})
    if not data:
        raise HTTPException(status_code=404, detail=f"Mastery not found: {kp_id}")
    return MasteryRecord.model_validate(data)


def update_mastery_from_homework(entry: HomeworkEntry) -> list[MasteryRecord]:
    updated: list[MasteryRecord] = []
    now = ISO()
    for p in entry.parts:
        if p.kind != "question" or not p.id:
            continue
        kp_id = f"q_{p.id}"
        label = (p.print_text or "未标注")[:40]
        hw = (p.handwriting or "").strip()
        mastery = "learning" if hw else "unknown"
        rec = MasteryRecord(
            kp_id=kp_id,
            label=label,
            mastery=mastery,
            updated_at=now,
            source_homework_id=entry.id,
            demo=True,
        )
        updated.append(upsert_mastery(rec))
    return updated

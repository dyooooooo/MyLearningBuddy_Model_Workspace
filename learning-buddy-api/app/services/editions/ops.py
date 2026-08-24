"""Edition ops service — catalog, drafts, publish, corrections, calibration."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    CorpusImportJob,
    Edition,
    EditionCorrectionQueue,
    EditionDraft,
    EditionVersion,
    Import35bJob,
)
from app.services.editions.bundle import (
    SEED_BASE,
    SEED_HUJIAO,
    clear_bundle_cache,
    editions_dir,
    list_seed_edition_ids,
    load_bundle,
)
from app.services.editions.authority import (
    authority_label,
    normalize_candidate_authority,
)
from app.services.editions.schema import EditionBundle, PolicyEntry

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _loads(raw: str, default: Any = None) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return default if default is not None else {}


def sync_seed_editions(session: Session) -> list[dict[str, Any]]:
    """Ensure DB catalog rows exist for every seed edition on disk."""
    out: list[dict[str, Any]] = []
    for eid in list_seed_edition_ids():
        bundle = load_bundle(eid)
        row = session.get(Edition, eid)
        if row is None:
            row = Edition(
                id=eid,
                display_name=bundle.meta.display_name or eid,
                base_id=bundle.meta.base or "",
                subject=bundle.meta.subject,
                grade_band=bundle.meta.grade_band,
                description=bundle.meta.description,
                published_version="seed",
                status="active",
                created_at=_now(),
                updated_at=_now(),
            )
            session.add(row)
        else:
            row.display_name = bundle.meta.display_name or row.display_name
            row.updated_at = _now()
        out.append(_edition_out(row, session))
    session.flush()
    return out


def _edition_out(row: Edition, session: Session) -> dict[str, Any]:
    job_count = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.edition_id == row.id)
        .count()
    )
    pending = (
        session.query(EditionCorrectionQueue)
        .filter(
            EditionCorrectionQueue.edition_id == row.id,
            EditionCorrectionQueue.status == "pending",
        )
        .count()
    )
    return {
        "id": row.id,
        "display_name": row.display_name,
        "base_id": row.base_id,
        "subject": row.subject,
        "grade_band": row.grade_band,
        "description": row.description,
        "published_version": row.published_version,
        "status": row.status,
        "job_count": job_count,
        "pending_corrections": pending,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def list_editions(session: Session) -> list[dict[str, Any]]:
    sync_seed_editions(session)
    rows = session.query(Edition).order_by(Edition.id).all()
    return [_edition_out(r, session) for r in rows]


def overview(session: Session) -> dict[str, Any]:
    editions = list_editions(session)
    pending = (
        session.query(EditionCorrectionQueue)
        .filter(EditionCorrectionQueue.status == "pending")
        .count()
    )
    return {
        "edition_count": len(editions),
        "editions": editions,
        "pending_corrections": pending,
        "seed_ids": list_seed_edition_ids(),
        "default_seed": SEED_HUJIAO,
        "base_id": SEED_BASE,
    }


def get_bundle_view(edition_id: str, version: str | None = None) -> dict[str, Any]:
    bundle = load_bundle(edition_id, version)
    return bundle.model_dump()


def get_or_create_draft(session: Session, edition_id: str) -> dict[str, Any]:
    draft = session.get(EditionDraft, edition_id)
    if draft is None:
        bundle = load_bundle(edition_id)
        draft = EditionDraft(
            edition_id=edition_id,
            bundle_json=_dumps(bundle.model_dump()),
            base_version="seed",
            updated_at=_now(),
        )
        session.add(draft)
        session.flush()
    return {
        "edition_id": draft.edition_id,
        "bundle": _loads(draft.bundle_json, {}),
        "base_version": draft.base_version,
        "updated_at": draft.updated_at,
        "updated_by": draft.updated_by,
    }


def save_draft(
    session: Session,
    edition_id: str,
    bundle: dict[str, Any],
    *,
    updated_by: str = "",
) -> dict[str, Any]:
    # Validate shape
    EditionBundle.model_validate(bundle)
    draft = session.get(EditionDraft, edition_id)
    if draft is None:
        draft = EditionDraft(edition_id=edition_id)
        session.add(draft)
    draft.bundle_json = _dumps(bundle)
    draft.updated_by = updated_by
    draft.updated_at = _now()
    session.flush()
    return get_or_create_draft(session, edition_id)


def publish_draft(
    session: Session,
    edition_id: str,
    *,
    version: str | None = None,
    note: str = "",
    published_by: str = "",
) -> dict[str, Any]:
    draft = session.get(EditionDraft, edition_id)
    if draft is None:
        raise ValueError(f"no draft for edition {edition_id}")
    bundle = _loads(draft.bundle_json, {})
    EditionBundle.model_validate(bundle)
    ver = version or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    row = EditionVersion(
        id=str(uuid.uuid4()),
        edition_id=edition_id,
        version=ver,
        bundle_json=draft.bundle_json,
        note=note,
        published_by=published_by,
        created_at=_now(),
    )
    session.add(row)
    # Materialize to disk for pipeline loaders
    _materialize_version(edition_id, ver, bundle)
    cat = session.get(Edition, edition_id)
    if cat is None:
        sync_seed_editions(session)
        cat = session.get(Edition, edition_id)
    if cat is not None:
        cat.published_version = ver
        cat.updated_at = _now()
    clear_bundle_cache()
    session.flush()
    return {
        "edition_id": edition_id,
        "version": ver,
        "published_at": row.created_at,
    }


def rollback_version(
    session: Session,
    edition_id: str,
    version: str,
) -> dict[str, Any]:
    row = (
        session.query(EditionVersion)
        .filter(
            EditionVersion.edition_id == edition_id,
            EditionVersion.version == version,
        )
        .first()
    )
    if row is None:
        raise ValueError(f"version not found: {edition_id}@{version}")
    bundle = _loads(row.bundle_json, {})
    draft = session.get(EditionDraft, edition_id)
    if draft is None:
        draft = EditionDraft(edition_id=edition_id)
        session.add(draft)
    draft.bundle_json = row.bundle_json
    draft.base_version = version
    draft.updated_at = _now()
    _materialize_version(edition_id, version, bundle)
    cat = session.get(Edition, edition_id)
    if cat is not None:
        cat.published_version = version
        cat.updated_at = _now()
    clear_bundle_cache()
    session.flush()
    return {"edition_id": edition_id, "version": version, "rolled_back": True}


def list_versions(session: Session, edition_id: str) -> list[dict[str, Any]]:
    rows = (
        session.query(EditionVersion)
        .filter(EditionVersion.edition_id == edition_id)
        .order_by(EditionVersion.created_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "edition_id": r.edition_id,
            "version": r.version,
            "note": r.note,
            "published_by": r.published_by,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def fork_edition(
    session: Session,
    *,
    source_id: str,
    new_id: str,
    display_name: str = "",
) -> dict[str, Any]:
    if session.get(Edition, new_id) is not None:
        raise ValueError(f"edition already exists: {new_id}")
    src_dir = editions_dir() / source_id
    dst_dir = editions_dir() / new_id
    if dst_dir.exists():
        raise ValueError(f"seed dir already exists: {new_id}")
    bundle = load_bundle(source_id)
    data = bundle.model_dump()
    data["meta"]["id"] = new_id
    data["meta"]["display_name"] = display_name or new_id
    data["meta"]["base"] = source_id if source_id != SEED_BASE else SEED_BASE
    # Write seed files
    dst_dir.mkdir(parents=True, exist_ok=False)
    (dst_dir / "assets").mkdir(parents=True, exist_ok=True)
    (dst_dir / "meta.json").write_text(
        _dumps(data["meta"]) + "\n", encoding="utf-8"
    )
    (dst_dir / "config.json").write_text(
        _dumps(data["config"]) + "\n", encoding="utf-8"
    )
    (dst_dir / "policy.json").write_text(
        _dumps(data["policy"]) + "\n", encoding="utf-8"
    )
    src_css = src_dir / "assets" / "theme.css"
    if src_css.is_file():
        (dst_dir / "assets" / "theme.css").write_text(
            src_css.read_text(encoding="utf-8"), encoding="utf-8"
        )
    clear_bundle_cache()
    sync_seed_editions(session)
    # Seed draft
    save_draft(session, new_id, data)
    return get_or_create_draft(session, new_id)


def _materialize_version(edition_id: str, version: str, bundle: dict[str, Any]) -> None:
    root = editions_dir() / edition_id / "versions" / version
    root.mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(_dumps(bundle.get("meta", {})) + "\n", encoding="utf-8")
    (root / "config.json").write_text(_dumps(bundle.get("config", {})) + "\n", encoding="utf-8")
    (root / "policy.json").write_text(_dumps(bundle.get("policy", {})) + "\n", encoding="utf-8")
    # Also update live seed config/policy so loaders without version pin see latest publish
    live = editions_dir() / edition_id
    if live.is_dir():
        (live / "config.json").write_text(_dumps(bundle.get("config", {})) + "\n", encoding="utf-8")
        (live / "policy.json").write_text(_dumps(bundle.get("policy", {})) + "\n", encoding="utf-8")
        if bundle.get("meta"):
            (live / "meta.json").write_text(_dumps(bundle["meta"]) + "\n", encoding="utf-8")


def enqueue_correction(
    session: Session,
    *,
    edition_id: str,
    candidate: dict[str, Any],
    source: str = "feedback",
    source_ref: str = "",
) -> dict[str, Any]:
    cand = normalize_candidate_authority(dict(candidate or {}))
    # Validate PolicyEntry shape (authority/tags kept on stored cand).
    PolicyEntry.model_validate(
        {
            "id": cand.get("id"),
            "facet": cand.get("facet"),
            "severity": cand.get("severity") or "should",
            "statement": cand.get("statement"),
            "tags": cand.get("tags") or [],
            "authority": cand.get("authority") or "edition",
        }
    )
    row = EditionCorrectionQueue(
        id=str(uuid.uuid4()),
        edition_id=edition_id,
        source=source,
        source_ref=source_ref,
        candidate_json=_dumps(cand),
        status="pending",
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return _corr_out(row)


def list_corrections(
    session: Session,
    *,
    edition_id: str | None = None,
    status: str | None = "pending",
) -> list[dict[str, Any]]:
    q = session.query(EditionCorrectionQueue)
    if edition_id:
        q = q.filter(EditionCorrectionQueue.edition_id == edition_id)
    if status:
        q = q.filter(EditionCorrectionQueue.status == status)
    rows = q.order_by(EditionCorrectionQueue.created_at.desc()).limit(200).all()
    return [_corr_out(r) for r in rows]


def _corr_out(row: EditionCorrectionQueue) -> dict[str, Any]:
    cand = _loads(row.candidate_json, {})
    if not isinstance(cand, dict):
        cand = {}
    auth = str(cand.get("authority") or "edition")
    return {
        "id": row.id,
        "edition_id": row.edition_id,
        "source": row.source,
        "source_ref": row.source_ref,
        "candidate": cand,
        "authority": auth,
        "authority_label": authority_label(auth),
        "title": cand.get("statement") or cand.get("id") or row.id,
        "summary": cand.get("statement") or "",
        "kind": cand.get("facet") or row.source,
        "status": row.status,
        "reviewer": row.reviewer,
        "review_note": row.review_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def review_correction(
    session: Session,
    corr_id: str,
    *,
    action: str,
    reviewer: str = "",
    review_note: str = "",
) -> dict[str, Any]:
    row = session.get(EditionCorrectionQueue, corr_id)
    if row is None:
        raise ValueError(f"correction not found: {corr_id}")
    if action not in ("approve", "reject"):
        raise ValueError("action must be approve|reject")
    row.status = "approved" if action == "approve" else "rejected"
    row.reviewer = reviewer
    row.review_note = review_note
    row.updated_at = _now()
    if action == "approve":
        cand = _loads(row.candidate_json, {})
        if not isinstance(cand, dict):
            cand = {}
        auth = str(cand.get("authority") or "edition")
        draft_view = get_or_create_draft(session, row.edition_id)
        bundle = dict(draft_view["bundle"])
        if auth == "pipeline":
            # 越权：记入 backlog，不并入 prompt policy（需改核心代码，不是方案条目）
            meta = dict(bundle.get("meta") or {})
            backlog = list(meta.get("pipeline_backlog") or [])
            backlog.append(
                {
                    "correction_id": row.id,
                    "approved_at": _now(),
                    "reviewer": reviewer,
                    "note": review_note,
                    "candidate": {
                        k: cand[k]
                        for k in (
                            "id",
                            "facet",
                            "severity",
                            "statement",
                            "tags",
                            "authority",
                            "page",
                            "paths",
                            "mode",
                        )
                        if k in cand
                    },
                }
            )
            meta["pipeline_backlog"] = backlog[-200:]
            bundle["meta"] = meta
        else:
            # 非越权：并入草稿 policy，发布后可注入提示词槽位
            policy = dict(bundle.get("policy") or {"version": "1", "entries": []})
            entries = list(policy.get("entries") or [])
            entries = [e for e in entries if e.get("id") != cand.get("id")]
            # Strip non-PolicyEntry keys that confuse prompt consumers later
            entry = {
                "id": cand.get("id"),
                "facet": cand.get("facet"),
                "severity": cand.get("severity") or "should",
                "statement": cand.get("statement"),
                "tags": list(cand.get("tags") or []),
                "authority": "edition",
            }
            entries.append(entry)
            policy["entries"] = entries
            bundle["policy"] = policy
        save_draft(session, row.edition_id, bundle, updated_by=reviewer)
    session.flush()
    return _corr_out(row)


def suggest_calibration(
    *,
    edition_id: str,
    display_name: str = "",
    sample_bboxes: list[list[float]] | None = None,
) -> dict[str, Any]:
    """Cold-start: suggest geometry from sample bboxes + fork from _base."""
    from app.services.ir.column_detect import detect_columns

    base = load_bundle(SEED_BASE)
    data = base.model_dump()
    data["meta"]["id"] = edition_id
    data["meta"]["display_name"] = display_name or edition_id
    data["meta"]["base"] = SEED_BASE
    suggestion: dict[str, Any] = {
        "edition_id": edition_id,
        "from": SEED_BASE,
        "geometry": data["config"]["geometry"],
        "layout": None,
    }
    if sample_bboxes:
        layout = detect_columns(sample_bboxes, edition_id=SEED_BASE)
        suggestion["layout"] = {
            "column_count": layout.column_count,
            "method": layout.method,
            "main_x0": layout.main_x0,
            "main_x1": layout.main_x1,
            "has_left": any(b.zone == "aside_left" for b in layout.bands),
            "has_right": any(b.zone == "aside_right" for b in layout.bands),
        }
        # Auto-tune zone edges from detected bands
        for band in layout.bands:
            if band.zone == "aside_left":
                data["config"]["geometry"]["aside_left_x1_max"] = band.x1
                data["config"]["zones"]["aside_left_x1_max"] = band.x1
                data["config"]["features"]["aside"] = "both"
            if band.zone == "aside_right":
                data["config"]["geometry"]["aside_right_x0_min"] = band.x0
                data["config"]["zones"]["aside_right_x0_min"] = band.x0
                data["config"]["features"]["aside"] = "both"
        suggestion["geometry"] = data["config"]["geometry"]
    # Candidate policy stubs
    candidates = [
        {
            "id": f"{edition_id}.zone.auto",
            "facet": "zone_hints",
            "severity": "should",
            "statement": "Auto-suggested zone edges from calibration sample pages.",
            "tags": ["calibration"],
        }
    ]
    suggestion["bundle"] = data
    suggestion["policy_candidates"] = candidates
    return suggestion


def run_golden_lite(edition_id: str) -> dict[str, Any]:
    """Lightweight golden: load bundle + assemble key slots + config hash."""
    import hashlib

    from app.services.editions.assembler import assemble_prompt

    bundle = load_bundle(edition_id)
    checks: list[dict[str, Any]] = []
    for slot_id in (
        "mineru.content_correct",
        "mineru.layout_rebuild",
        "mineru.unit_judge",
        "b35.vision_layout",
    ):
        try:
            text = assemble_prompt(slot_id, edition_id)
            checks.append(
                {
                    "slot": slot_id,
                    "ok": len(text) > 20,
                    "chars": len(text),
                }
            )
        except Exception as exc:
            checks.append({"slot": slot_id, "ok": False, "error": str(exc)})
    payload = json.dumps(bundle.config.model_dump(), sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    ok = all(c.get("ok") for c in checks)
    return {
        "edition_id": edition_id,
        "ok": ok,
        "config_sha": digest,
        "checks": checks,
        "policy_entries": len(bundle.policy.entries),
    }


def job_edition_stats(session: Session) -> dict[str, Any]:
    mineru = session.query(CorpusImportJob.edition_id).all()
    b35 = session.query(Import35bJob.profile_id).all()
    counts: dict[str, int] = {}
    for (eid,) in mineru:
        key = eid or "(empty)"
        counts[key] = counts.get(key, 0) + 1
    for (pid,) in b35:
        key = f"35b:{pid or '(empty)'}"
        counts[key] = counts.get(key, 0) + 1
    return {"by_edition": counts}

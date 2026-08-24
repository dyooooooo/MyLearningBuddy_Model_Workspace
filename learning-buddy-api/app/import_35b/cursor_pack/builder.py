"""Assemble a Cursor Repair Pack directory from feedback + MinIO debug artifacts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.config import settings
from app.db.models import Import35bFeedback
from app.import_35b import store
from app.import_35b.cursor_pack.diff_hints import build_diff_hints
from app.import_35b.cursor_pack.triage import triage_feedback
from app.import_35b.debug_bundle import PAGE_IMAGE_FILE
from app.util.json_util import loads

logger = logging.getLogger(__name__)

_EVIDENCE_FILES = {
    "raw_model.json": "raw_model.json",
    "blueprint.json": "blueprint.json",
    "blueprint_post_repair.json": "blueprint_post_repair.json",
    "blueprint_post_boundary.json": "blueprint_post_boundary.json",
    "page.html": "page.html",
    "stage_changelog.json": "stage_changelog.json",
}


def _pack_root(feedback_id: str) -> Path:
    base = Path(settings.import35b_cursor_pack_dir or "/data/buddy/cursor-packs")
    return base / feedback_id


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    _write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _fetch_debug_file(debug_prefix: str, filename: str) -> bytes | None:
    return get_object_bytes(f"{debug_prefix.rstrip('/')}/{filename}")


def _golden_stub(feedback: Import35bFeedback, triage_layer: str) -> dict[str, Any]:
    return {
        "case_id": f"feedback_{feedback.id[:8]}_p{feedback.page_no}",
        "page_no": feedback.page_no,
        "feedback_id": feedback.id,
        "job_id": feedback.job_id,
        "layer": triage_layer,
        "note": feedback.body[:200],
        "expect": {"t1_hard_findings": []},
    }


def build_cursor_pack(session: Session, feedback: Import35bFeedback) -> dict[str, Any]:
    """Write pack to disk and update feedback.pack_path."""
    debug_prefix = feedback.debug_prefix
    if not debug_prefix:
        raise ValueError("feedback missing debug_prefix")

    tags = loads(feedback.tags_json, [])
    t1 = loads(feedback.t1_snapshot_json, [])
    triage = triage_feedback(t1_snapshot=t1, user_body=feedback.body, tags=tags)

    root = _pack_root(feedback.id)
    if root.exists():
        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
    root.mkdir(parents=True, exist_ok=True)

    # Images
    if feedback.screenshot_key:
        shot = get_object_bytes(feedback.screenshot_key)
        if shot:
            _write_bytes(root / "images" / "user_screenshot.jpg", shot)
    input_key = feedback.input_page_key or f"{debug_prefix}/{PAGE_IMAGE_FILE}"
    inp = get_object_bytes(input_key)
    if inp:
        _write_bytes(root / "images" / "input_page.jpg", inp)

    # Evidence
    raw_obj: dict | None = None
    repaired_obj: dict | None = None
    blueprint_obj: dict | None = None
    for out_name, minio_name in _EVIDENCE_FILES.items():
        data = _fetch_debug_file(debug_prefix, minio_name)
        if data:
            _write_bytes(root / "evidence" / out_name, data)
            if out_name == "raw_model.json":
                try:
                    raw_obj = json.loads(data)
                except json.JSONDecodeError:
                    pass
            if out_name == "blueprint_post_boundary.json":
                try:
                    repaired_obj = json.loads(data)
                except json.JSONDecodeError:
                    pass
            if out_name == "blueprint.json":
                try:
                    blueprint_obj = json.loads(data)
                except json.JSONDecodeError:
                    pass

    meta_bytes = _fetch_debug_file(debug_prefix, "debug.json")
    if meta_bytes:
        _write_bytes(root / "evidence" / "debug_meta.json", meta_bytes)
    _write_json(root / "evidence" / "t1_findings.json", t1)

    # MinerU layout hints + runtime features (when available on the job)
    job = store.get_job(session, feedback.job_id)
    if job is not None and job.minio_key:
        from app.import_35b.assets import asset_prefix_from_minio_key

        prefix = asset_prefix_from_minio_key(job.minio_key).rstrip("/")
        page_tag = f"p{int(feedback.page_no):04d}"
        mineru_key = f"{prefix}/35b/mineru/by_page/{page_tag}.json"
        mineru_bytes = get_object_bytes(mineru_key)
        if mineru_bytes:
            _write_bytes(root / "evidence" / "mineru_layout.json", mineru_bytes)
        job_meta = loads(getattr(job, "meta_json", None) or "{}", {})
        features = job_meta.get("features")
        if features is None and isinstance(job_meta.get("layout_stats"), dict):
            features = {"layout_stats": job_meta.get("layout_stats")}
        if features is not None:
            _write_json(root / "evidence" / "features.json", features)

    page_slug = f"p{int(feedback.page_no):04d}"
    replay_dir = root / "evidence" / page_slug
    replay_dir.mkdir(parents=True, exist_ok=True)
    raw_bytes = _fetch_debug_file(debug_prefix, "raw_model.json")
    if raw_bytes:
        (replay_dir / "raw_model.json").write_bytes(raw_bytes)

    diff_md = build_diff_hints(raw_obj, repaired_obj, blueprint_obj)
    _write_text(root / "analysis" / "diff_hints.md", diff_md)

    t1_types = sorted({str(f.get("suspect_type")) for f in t1 if isinstance(f, dict) and f.get("suspect_type")})
    stage_summary = (
        f"# Stage summary\n\n"
        f"- prompt_version: `{feedback.prompt_version}`\n"
        f"- T1 types: {', '.join(t1_types) or '（无）'}\n"
        f"- triage layer: **{triage.layer}** ({triage.confidence})\n"
        f"- {triage.rationale}\n"
    )
    _write_text(root / "analysis" / "stage_summary.md", stage_summary)

    ticket = store.feedback_to_dict(feedback)
    _write_json(root / "ticket.json", ticket)

    files_list = "\n".join(f"- `{f}`" for f in triage.suggested_files)
    pack_rel = f"data/cursor-packs/{feedback.id}"
    readme = f"""# 35B Repair Pack · {feedback.id}

## 现象（用户）
- 任务: {feedback.label} · job `{feedback.job_id}` · 第 {feedback.page_no} 页
- 描述: {feedback.body}
- 截图: [images/user_screenshot.jpg](images/user_screenshot.jpg)

## 系统体检（T1）
- 共 {len(t1)} 条: {', '.join(t1_types) or '无'}
- 详情: [evidence/t1_findings.json](evidence/t1_findings.json)

## 初步归因（执行前请确认）
- 层级: **{triage.layer}**（置信度 {triage.confidence}）
- 依据: {triage.rationale}
- 优先文件:
{files_list}

## 证据链
1. [images/user_screenshot.jpg](images/user_screenshot.jpg)
2. [evidence/raw_model.json](evidence/raw_model.json)
3. [evidence/blueprint.json](evidence/blueprint.json)
4. [evidence/page.html](evidence/page.html)
5. [analysis/diff_hints.md](analysis/diff_hints.md)

## 入口
- 计划: [{triage.entry_doc}]({triage.entry_doc})
- 验收: [commands.sh](commands.sh)
"""
    _write_text(root / "README.md", readme)

    exec_plan = f"""# 35B Repair Plan · {feedback.id}

> 来源: {feedback.label} p{feedback.page_no} · prompt `{feedback.prompt_version}`

## 目标
修复第 {feedback.page_no} 页：{feedback.body[:120]}

## 约束
- 只改 35B 线：`learning-buddy-api/app/import_35b/**`（及 Studio 渲染如需）
- 禁止动 exam-ocr ③ / 旧 HITL 裁决路径
- 改 pipeline 后：`docker compose build buddy-api buddy-worker` 并 `up -d`
- 必须补 golden + 跑 acceptance

## 归因
- 层级: **{triage.layer}**（{triage.confidence}）
- {triage.rationale}

## 执行步骤

### Step 0 — 优先运行时修复（先于改代码）
- [ ] 优先试 **PageReprocess**（`POST .../pages/{feedback.page_no}/reprocess`，mode=full 或 from_raw）
- [ ] 单块问题试 **BlockRefine**（`POST .../blocks/{{block_id}}/refine`）
- [ ] 仍失败再改 Profile features / v6 prompt / heuristics

### Step 1 — 复现（只读）
- [ ] 读 `@images/user_screenshot.jpg`
- [ ] 读 `@evidence/raw_model.json` `@evidence/blueprint.json` `@evidence/page.html`
- [ ] 若有：`@evidence/mineru_layout.json` `@evidence/features.json`
- [ ] `bash commands.sh reproduce`

### Step 2 — 修改
{files_list}

### Step 3 — Harness
- [ ] 将 `harness/golden_stub.jsonl` 合并入 `learning-buddy-api/data/eval/`
- [ ] `bash commands.sh acceptance`

### Step 4 — 部署验证
- [ ] 重建 buddy-api / buddy-worker
- [ ] 重导该教材或 `--page_limit` 单页；确认 p{feedback.page_no}

## 完成标准
- [ ] golden / replay 通过
- [ ] 本页 T1 硬错误消除（或 golden 显式 expect）
- [ ] 用户描述的问题在预览中消失
"""
    _write_text(root / "EXEC.plan.md", exec_plan)

    plan_prompt = f"""# Plan 生成任务

你是 35B pipeline 工程师。基于本目录：

1. 读 [README.md](README.md) 与 [analysis/diff_hints.md](analysis/diff_hints.md)
2. 确认或修正归因（当前: {triage.layer} / {triage.confidence}）
3. **重写 [EXEC.plan.md](EXEC.plan.md)** 的 Step 2–3：精确到文件与函数
4. 只写 plan，不写代码；完成后等待用户确认再执行

约束见 EXEC.plan.md「约束」节。
"""
    _write_text(root / "PLAN.prompt.md", plan_prompt)

    stub_line = json.dumps(_golden_stub(feedback, triage.layer), ensure_ascii=False)
    _write_text(root / "harness" / "golden_stub.jsonl", stub_line + "\n")

    commands = f"""#!/usr/bin/env bash
# Cursor Repair Pack · {feedback.id}
set -euo pipefail
PACK_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
API_ROOT="$(cd "$PACK_DIR/../../learning-buddy-api" 2>/dev/null && pwd || echo "")"

reproduce() {{
  if [[ -z "$API_ROOT" || ! -d "$API_ROOT" ]]; then
    echo "learning-buddy-api not found relative to pack; open evidence/ manually"
    return 1
  fi
  cd "$API_ROOT"
  PYTHONPATH=. python3 scripts/replay-import35b.py --dir "$PACK_DIR/evidence" --json /tmp/replay-{feedback.id}.json
}}

acceptance() {{
  if [[ -z "$API_ROOT" || ! -d "$API_ROOT" ]]; then
    echo "skip acceptance — API root missing"
    return 0
  fi
  cd "$API_ROOT"
  if [[ -x scripts/acceptance-import35b-v5.sh ]]; then
    bash scripts/acceptance-import35b-v5.sh
  else
    PYTHONPATH=. python3 -m pytest tests/test_import_35b_v4_arch.py -q
  fi
}}

case "${{1:-help}}" in
  reproduce) reproduce ;;
  acceptance) acceptance ;;
  *) echo "Usage: $0 reproduce|acceptance" ;;
esac
"""
    _write_text(root / "commands.sh", commands)

    manifest = {
        "feedback_id": feedback.id,
        "job_id": feedback.job_id,
        "page_no": feedback.page_no,
        "pack_path": pack_rel,
        "plan_mode": triage.plan_mode,
        "triage_confidence": triage.confidence,
        "entry_doc": triage.entry_doc,
        "layer": triage.layer,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(root / "manifest.json", manifest)

    pack_path = pack_rel
    store.set_feedback_status(session, feedback, pack_path=str(root))
    logger.info("cursor pack built feedback=%s path=%s", feedback.id, root)
    return {
        "ok": True,
        "feedback_id": feedback.id,
        "pack_path": pack_path,
        "absolute_path": str(root),
        "manifest": manifest,
    }

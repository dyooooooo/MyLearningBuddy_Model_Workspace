"""方案（Edition）工具插件：只读查询 + 草稿修改 + 发布（闸门档位在 permissions.yaml）。"""

from __future__ import annotations

import json
from typing import Any

from ..registry import ToolContext, ToolSpec, register


def _set_by_path(obj: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    client = ctx.client

    def editions_overview() -> Any:
        """列出全部教材方案（Edition）概览：id、名称、学科学段、发布版本、任务数。"""
        return client.editions_overview()

    def editions_bundle(edition_id: str, version: str = "") -> Any:
        """取某个方案的已发布 Bundle（meta/config/policy/assets 全量 JSON）。"""
        return client.get_bundle(edition_id, version or None)

    def editions_draft(edition_id: str) -> Any:
        """取某个方案的当前草稿（未发布，存于 Postgres）。"""
        return client.get_draft(edition_id)

    def editions_calibrate(edition_id: str, sample_bboxes_json: str = "[]") -> Any:
        """几何标定建议（只读，不落盘）。sample_bboxes_json 为 [[x0,y0,x1,y1], ...] JSON 字符串。"""
        try:
            bboxes = json.loads(sample_bboxes_json or "[]")
        except json.JSONDecodeError as e:
            return {"error": f"sample_bboxes_json 非法: {e}"}
        if bboxes is not None and not isinstance(bboxes, list):
            return {"error": "sample_bboxes_json 须为数组"}
        return client.calibrate(edition_id, bboxes or None, persist_fork=False)

    def editions_fork(source_id: str, new_id: str, display_name: str = "") -> Any:
        """从基座方案 fork 一个新方案草稿（Postgres 草稿，可回滚）。"""
        return client.fork_edition(source_id, new_id, display_name)

    def editions_save_draft(edition_id: str, patch_json: str = "", bundle_json: str = "") -> Any:
        """保存方案草稿修改。二选一：
        patch_json —— 点路径→值的 JSON 对象（如 {"config.geometry.aside_x_ratio": 0.72}）；
        bundle_json —— 整包 bundle JSON（覆盖式）。"""
        geom_touch = False
        if bundle_json:
            bundle = json.loads(bundle_json)
            geom_touch = True
        elif patch_json:
            patch = json.loads(patch_json)
            if not isinstance(patch, dict):
                return {"error": "patch_json 须为 JSON 对象"}
            geom_touch = any("geometry" in str(k) or "policy" in str(k) for k in patch)
            draft = client.get_draft(edition_id)
            bundle = draft.get("bundle") or {}
            for path, value in patch.items():
                _set_by_path(bundle, path, value)
        else:
            return {"error": "需要 patch_json（点路径→值）或 bundle_json（整包）"}
        out = client.save_draft(edition_id, bundle)
        if geom_touch and ctx.session_id and ctx.kind == "import_advice":
            try:
                from ...segment_state import append_layout_change

                sess = ctx.store.get_session(ctx.session_id) or {}
                pl = dict(sess.get("payload") or {})
                pl["layout_change_log"] = append_layout_change(
                    pl,
                    {
                        "op": "edition_geometry",
                        "page": None,
                        "summary": f"save_draft {edition_id}",
                        "before_hint": "",
                        "after_hint": (patch_json or bundle_json or "")[:300],
                        "proposer": "ai",
                    },
                )
                ctx.store.update_session(ctx.session_id, payload=pl)
            except Exception:  # noqa: BLE001
                pass
        return {"edition_id": edition_id, "updated_at": out.get("updated_at")}

    def editions_publish(edition_id: str, note: str = "") -> Any:
        """发布方案草稿为正式版本（闸门：需用户批准）。"""
        return client.publish_edition(edition_id, note=note)

    def edition_record_effective_change(
        edition_id: str,
        summary: str,
        candidate_json: str = "{}",
        source: str = "ai_import",
        source_ref: str = "",
        page: int = 0,
        kind: str = "",
    ) -> Any:
        """将「已执行且确认有效」的建议记入方案 correction 队列（不覆盖原方案）。"""
        try:
            candidate = json.loads(candidate_json or "{}")
        except json.JSONDecodeError as e:
            return {"error": f"candidate_json 非法: {e}"}
        if not isinstance(candidate, dict):
            candidate = {}
        candidate.setdefault("summary", summary)
        if page:
            candidate.setdefault("page", int(page))
        if kind:
            candidate.setdefault("kind", kind)
        out = client.enqueue_correction(
            edition_id, candidate, source=source or "ai_import", source_ref=source_ref
        )
        if ctx.session_id:
            try:
                sess = ctx.store.get_session(ctx.session_id) or {}
                pl = dict(sess.get("payload") or {})
                from ...segment_state import append_effective_change

                pl["effective_changes"] = append_effective_change(
                    pl,
                    {
                        "page": page or None,
                        "kind": kind or "policy",
                        "proposer": "ai" if source == "ai_import" else "user",
                        "summary": summary,
                        "action_taken": str(candidate.get("action") or "")[:200],
                        "correction_id": out.get("id") or out.get("corr_id"),
                        "candidate": {
                            k: candidate[k]
                            for k in ("id", "facet", "severity", "statement", "tags")
                            if k in candidate
                        }
                        or None,
                        "layout_log_ids": candidate.get("layout_log_ids"),
                    },
                )
                ctx.store.update_session(ctx.session_id, payload=pl)
            except Exception:  # noqa: BLE001
                pass
        return out

    def edition_finalize_from_session(
        source_edition_id: str = "",
        new_id: str = "",
        display_name: str = "",
    ) -> Any:
        """根据会话 layout_change_log + effective_changes fork 新排版方案草稿。"""
        if not ctx.session_id:
            return {"error": "无会话"}
        sess = ctx.store.get_session(ctx.session_id) or {}
        pl = dict(sess.get("payload") or {})
        src = (source_edition_id or pl.get("source_edition_id") or pl.get("edition_id") or "").strip()
        changes = [c for c in (pl.get("effective_changes") or []) if isinstance(c, dict)]
        layout_log = [c for c in (pl.get("layout_change_log") or []) if isinstance(c, dict)]
        if not src:
            return {"error": "缺少 source_edition_id"}
        if not changes and not layout_log:
            return {
                "forked": False,
                "reason": "无排版改动账本，沿用原方案",
                "source_edition_id": src,
            }
        from datetime import date

        nid = (new_id or "").strip() or f"{src}_ai_{date.today().strftime('%Y%m%d')}"
        name = display_name or f"{src} · AI 分段修订"
        fork_out = client.fork_edition(src, nid, name)
        draft = client.get_draft(nid)
        bundle = draft.get("bundle") or client.get_bundle(src).get("bundle") or {}
        meta = bundle.setdefault("meta", {})
        if not isinstance(meta, dict):
            meta = {}
            bundle["meta"] = meta

        # 全量中间排版改动（截断过长字段）
        slim_log = []
        for c in layout_log[:200]:
            slim_log.append(
                {
                    "id": c.get("id"),
                    "at": c.get("at"),
                    "page": c.get("page"),
                    "op": c.get("op"),
                    "summary": str(c.get("summary") or "")[:200],
                    "before_hint": str(c.get("before_hint") or "")[:200],
                    "after_hint": str(c.get("after_hint") or "")[:200],
                    "proposer": c.get("proposer"),
                }
            )
        meta["layout_change_log"] = slim_log

        notes = meta.setdefault("ai_import_notes", [])
        if not isinstance(notes, list):
            notes = []
            meta["ai_import_notes"] = notes
        for c in changes:
            notes.append(
                {
                    "summary": c.get("summary"),
                    "page": c.get("page"),
                    "kind": c.get("kind"),
                    "proposer": c.get("proposer"),
                }
            )

        # 合并可结构化的 PolicyEntry（candidate 曾入 correction 队列时可能挂在 effective_changes）
        policy = bundle.setdefault("policy", {})
        if not isinstance(policy, dict):
            policy = {}
            bundle["policy"] = policy
        entries = policy.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
            policy["entries"] = entries
        existing_ids = {str(e.get("id")) for e in entries if isinstance(e, dict)}
        for c in changes:
            cand = c.get("candidate") if isinstance(c.get("candidate"), dict) else None
            # enqueue 时 candidate 未必回写会话；用 summary 生成一条 layout 提示策略
            if cand and cand.get("id") and cand.get("facet") and cand.get("statement"):
                if str(cand["id"]) not in existing_ids:
                    entries.append(
                        {
                            "id": str(cand["id"])[:64],
                            "facet": cand["facet"],
                            "severity": cand.get("severity") or "should",
                            "statement": str(cand["statement"])[:800],
                            "tags": list(cand.get("tags") or ["ai_import"]),
                        }
                    )
                    existing_ids.add(str(cand["id"]))
            elif c.get("summary") and c.get("kind") in ("layout", "policy", "geometry", ""):
                eid = f"ai_{c.get('id') or len(entries)}"
                if eid not in existing_ids:
                    entries.append(
                        {
                            "id": eid[:64],
                            "facet": "zone_hints",
                            "severity": "should",
                            "statement": str(c.get("summary"))[:800],
                            "tags": ["ai_import", str(c.get("kind") or "layout")],
                        }
                    )
                    existing_ids.add(eid)

        client.save_draft(nid, bundle)
        pl["result_edition_id"] = nid
        pl["source_edition_id"] = src
        ctx.store.update_session(ctx.session_id, payload=pl)
        from ...next_steps import next_steps_message

        msg = next_steps_message(
            pl,
            preface=(
                f"已形成新排版方案草稿 `{nid}`（源自 `{src}`），"
                f"含 layout_change_log={len(slim_log)} 条、"
                f"effective_notes={len(changes)} 条。"
                "当前导入任务仍绑原方案；可在方案页审阅后发布。"
            ),
        )
        ctx.store.add_message(ctx.session_id, "system", msg)
        return {
            "forked": True,
            "result_edition_id": nid,
            "source_edition_id": src,
            "layout_change_count": len(slim_log),
            "effective_change_count": len(changes),
            "fork": fork_out,
        }

    return [
        ToolSpec("editions.overview", "方案清单", editions_overview),
        ToolSpec("editions.bundle", "取已发布 Bundle", editions_bundle),
        ToolSpec("editions.draft", "取方案草稿", editions_draft),
        ToolSpec("editions.calibrate", "几何标定建议（只算不落盘）", editions_calibrate),
        ToolSpec(
            "edition.record_effective_change",
            "记录确认有效的建议到方案 correction",
            edition_record_effective_change,
        ),
        ToolSpec(
            "edition.finalize_from_session",
            "按有效建议 fork 新方案草稿",
            edition_finalize_from_session,
        ),
        ToolSpec("editions.fork", "fork 新方案草稿", editions_fork),
        ToolSpec("editions.save_draft", "保存方案草稿", editions_save_draft),
        ToolSpec("editions.publish", "发布方案", editions_publish),
    ]

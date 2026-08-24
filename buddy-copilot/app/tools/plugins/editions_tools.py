"""方案（Edition）工具插件：只读查询 + 草稿修改 + 发布（闸门档位在 permissions.yaml）。"""

from __future__ import annotations

import json
from typing import Any

from ..registry import ToolContext, ToolSpec, register


def edition_dir_rel(edition_id: str) -> str:
    """教材方案在工作区里的相对目录（给 fs_guard 做前缀求交）。"""
    eid = (edition_id or "").strip().strip("/")
    return f"learning-buddy-api/configs/editions/{eid}/"


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

    def _grant_edition_dir(edition_id: str, *, reason: str) -> None:
        if not ctx.session_id or not edition_id:
            return
        try:
            ctx.store.grant_write_scope(
                ctx.session_id, [edition_dir_rel(edition_id)], reason=reason
            )
        except Exception:  # noqa: BLE001 - 记账失败不该让 fork 本身失败
            pass

    def editions_fork(source_id: str, new_id: str, display_name: str = "") -> Any:
        """从基座方案 fork 一个新方案草稿（Postgres 草稿，可回滚）。"""
        out = client.fork_edition(source_id, new_id, display_name)
        if not (isinstance(out, dict) and out.get("error")):
            _grant_edition_dir(new_id, reason=f"editions.fork `{new_id}`")
        return out

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
        authority: str = "",
    ) -> Any:
        """将「已执行且确认有效」的建议记入方案 correction 队列（不覆盖原方案）。

        authority: edition=非越权（可进方案数据）；pipeline=越权（需改 Pipeline 核心）。
        """
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
        from ...authority import authority_label, classify_authority, stamp_authority

        auth = classify_authority(
            mode=str(candidate.get("mode") or kind or ""),
            paths=list(candidate.get("paths") or [])
            if isinstance(candidate.get("paths"), list)
            else None,
            explicit=authority or str(candidate.get("authority") or ""),
        )
        candidate["authority"] = auth
        if not candidate.get("facet"):
            candidate["facet"] = (
                "layout_rebuild" if auth == "edition" else "content_correct"
            )
        if not candidate.get("statement"):
            candidate["statement"] = summary
        if not candidate.get("id"):
            candidate["id"] = f"ai.{page or 0}.{auth}"
        # Prefer fork result / session binding so pending queue matches UI target.
        if ctx.session_id:
            try:
                sess0 = ctx.store.get_session(ctx.session_id) or {}
                pl0 = sess0.get("payload") or {}
                preferred = str(
                    pl0.get("result_edition_id")
                    or edition_id
                    or pl0.get("edition_id")
                    or pl0.get("source_edition_id")
                    or ""
                ).strip()
                if preferred:
                    edition_id = preferred
            except Exception:  # noqa: BLE001
                pass
        # Confirm as Improvement Point (accepted + procedure + dual track)
        ip_out: dict[str, Any] = {}
        ip_id = ""
        if ctx.session_id:
            try:
                sess0 = ctx.store.get_session(ctx.session_id) or {}
                pl0 = dict(sess0.get("payload") or {})
                job_id = str(pl0.get("import_job_id") or pl0.get("job_id") or "").strip()
                fp = ""
                bf = pl0.get("book_fingerprint") or {}
                if isinstance(bf, dict):
                    fp = str(bf.get("hash") or "")
                layout_log = [
                    c for c in (pl0.get("layout_change_log") or []) if isinstance(c, dict)
                ]
                page_attempts = [
                    a
                    for a in layout_log
                    if int(a.get("page") or a.get("page_no") or 0) == int(page or 0)
                    or not page
                ]
                # Prefer attempts for this page; fall back to last few
                use_attempts = page_attempts[-12:] if page_attempts else layout_log[-8:]
                # 流水线/越权事件不进改进点表（只走待审议队列）。
                if auth != "pipeline":
                    # 默认不进抽象轨；只有候选里显式要求才 draft。
                    pattern_status = str(candidate.get("pattern_status") or "none")
                    # 来源闭集：ai_auto（全书自主）/ user_ask（用户让改）
                    if str(source or "") in ("ai_import", "ai_batch", "fullbook_ai") or (
                        "第" in summary and "批" in summary
                    ):
                        origin = "ai_auto"
                    else:
                        origin = "user_ask"
                    concrete_in = (
                        dict(candidate.get("concrete"))
                        if isinstance(candidate.get("concrete"), dict)
                        else {}
                    )
                    for key in (
                        "paths",
                        "detail",
                        "mode",
                        "proposer",
                        "user_input",
                        "user_prompt",
                        "authority_label",
                        "causal_chain",
                    ):
                        if candidate.get(key) not in (None, "", []) and key not in concrete_in:
                            concrete_in[key] = candidate.get(key)
                    if not concrete_in.get("mode") and (kind or candidate.get("mode")):
                        concrete_in["mode"] = str(candidate.get("mode") or kind or "")
                    if not concrete_in.get("paths") and isinstance(
                        candidate.get("paths"), list
                    ):
                        concrete_in["paths"] = candidate.get("paths")
                    concrete_in["origin"] = origin
                    ip_out = client.ip_confirm(
                        {
                            "job_id": job_id,
                            "session_id": ctx.session_id,
                            "fingerprint_hash": fp,
                            "page_no": int(page or 0),
                            "summary": summary,
                            "authority": auth,
                            "kind": kind or "",
                            "block_ids": list(candidate.get("block_ids") or [])
                            if isinstance(candidate.get("block_ids"), list)
                            else [],
                            "attempts": use_attempts,
                            "procedure": candidate.get("procedure")
                            if isinstance(candidate.get("procedure"), dict)
                            else None,
                            "concrete": concrete_in or None,
                            "pattern_abstract": str(candidate.get("pattern_abstract") or ""),
                            "pattern_status": pattern_status,
                            "edition_family_id": str(
                                pl0.get("source_edition_id") or pl0.get("edition_id") or ""
                            ),
                            "write_correction": True,
                            "origin": origin,
                        }
                    )
                    ip_id = str((ip_out.get("ip") or {}).get("id") or "")
            except Exception as e:  # noqa: BLE001
                return {
                    "error": f"IP confirm 失败（有效改进须含成功路径 procedure）: {e}",
                    "hint": "请确保同页已有 layout_change_log 工具步骤后再记入",
                }

        out = client.enqueue_correction(
            edition_id, candidate, source=source or "ai_import", source_ref=source_ref or ip_id
        )
        corr_id = str(out.get("id") or out.get("corr_id") or "").strip()
        if ctx.session_id:
            try:
                sess = ctx.store.get_session(ctx.session_id) or {}
                pl = dict(sess.get("payload") or {})
                from ...segment_state import append_effective_change

                stamped = stamp_authority(
                    {
                        "page": page or None,
                        "kind": kind or "policy",
                        "proposer": "ai" if source == "ai_import" else "user",
                        "summary": summary,
                        "action_taken": str(candidate.get("action") or "")[:200],
                        "correction_id": corr_id,
                        "ip_id": ip_id,
                        "edition_id": edition_id,
                        "authority": auth,
                        "candidate": {
                            k: candidate[k]
                            for k in (
                                "id",
                                "facet",
                                "severity",
                                "statement",
                                "tags",
                                "authority",
                            )
                            if k in candidate
                        }
                        or None,
                        "layout_log_ids": candidate.get("layout_log_ids"),
                        "procedure": (ip_out.get("ip") or {}).get("procedure"),
                    }
                )
                pl["effective_changes"] = append_effective_change(pl, stamped)
                ctx.store.update_session(ctx.session_id, payload=pl)
                tip = (
                    f"✅ 已确认改进点（accepted）并记入方案 `{edition_id}`「待审议」"
                    f"（{authority_label(auth)}）"
                    + (f"；ip={ip_id[:8]}…" if ip_id else "")
                    + (f"；correction={corr_id[:8]}…" if corr_id else "")
                    + "。含成功路径 procedure；可在方案页 → 改进点/待审中查看。"
                )
                ctx.store.add_message(
                    ctx.session_id,
                    "system",
                    {
                        "text": tip,
                        "actions": [
                            {
                                "id": "open_corrections",
                                "label": "打开待审议",
                                "send": f"__ui:open_corrections:{edition_id}",
                            },
                            {
                                "id": "open_improvement_points",
                                "label": "打开改进点",
                                "send": f"__ui:open_improvement_points:{edition_id}",
                            },
                        ],
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        return {**out, "ip": ip_out.get("ip"), "ip_id": ip_id}

    def edition_finalize_from_session(
        source_edition_id: str = "",
        new_id: str = "",
        display_name: str = "",
    ) -> Any:
        """根据会话 layout_change_log + effective_changes fork 新排版方案草稿。

        先 converge 改进点：未决 conflict 则阻塞 fork。
        """
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
        job_id = str(pl.get("import_job_id") or pl.get("job_id") or "").strip()
        fp = ""
        bf = pl.get("book_fingerprint") or {}
        if isinstance(bf, dict):
            fp = str(bf.get("hash") or "")
        # Hard gate: write attempts without accepted IPs cannot finalize
        write_n = len(layout_log)
        if write_n > 0 and job_id:
            try:
                listed = client.ip_list(job_id=job_id, status="accepted")
                accepted_n = int((listed.get("counts") or {}).get("accepted") or 0)
                if accepted_n <= 0 and not changes:
                    return {
                        "error": (
                            "有写盘账本但无 accepted 改进点：请先对有效改动调用 "
                            "edition.record_effective_change，再 finalize"
                        ),
                        "forked": False,
                        "layout_change_log_count": write_n,
                        "accepted_count": 0,
                    }
                if accepted_n <= 0 and changes:
                    return {
                        "error": (
                            "effective_changes 未落到 accepted IP："
                            "请用 edition.record_effective_change 确认后再 finalize"
                        ),
                        "forked": False,
                        "layout_change_log_count": write_n,
                        "effective_changes_count": len(changes),
                        "accepted_count": 0,
                    }
            except Exception as e:  # noqa: BLE001
                return {
                    "error": f"finalize 前查询改进点失败: {e}",
                    "forked": False,
                }
        try:
            # Sync remaining attempts then converge
            if layout_log and job_id:
                client.ip_ingest_attempts(
                    {
                        "attempts": layout_log[-80:],
                        "job_id": job_id,
                        "session_id": ctx.session_id,
                        "fingerprint_hash": fp,
                    }
                )
            conv = client.ip_converge(
                {
                    "job_id": job_id,
                    "session_id": ctx.session_id,
                    "fingerprint_hash": fp,
                }
            )
            if not conv.get("ok"):
                conflicts = conv.get("conflicts") or []
                return {
                    "error": "改进点 converge 未通过：存在未决 conflict，请先裁决",
                    "forked": False,
                    "conflicts": conflicts,
                    "converge": conv,
                }
        except Exception as e:  # noqa: BLE001
            return {
                "error": f"finalize 前 converge 失败: {e}",
                "forked": False,
            }
        from datetime import date, datetime

        base_nid = (new_id or "").strip() or f"{src}_ai_{date.today().strftime('%Y%m%d')}"
        name = display_name or f"{src} · AI 修订 {date.today().strftime('%Y%m%d')}"
        nid = base_nid
        fork_out: dict[str, Any] = {}
        # Avoid "already exists" lying as「已形成新草稿」— bump unique suffix.
        for attempt in range(6):
            try:
                fork_out = client.fork_edition(src, nid, name)
                break
            except Exception as e:  # noqa: BLE001
                err = str(e).lower()
                if "already exists" not in err:
                    return {"error": f"fork 失败: {e}", "forked": False}
                stamp = datetime.now().strftime("%H%M%S")
                nid = (
                    f"{base_nid}_{stamp}"
                    if attempt == 0
                    else f"{base_nid}_{stamp}_{attempt}"
                )
                name = f"{display_name or src} · AI 修订 {stamp}"
        else:
            return {
                "error": f"fork 失败：方案 id 冲突（尝试 {base_nid}）",
                "forked": False,
            }
        _grant_edition_dir(nid, reason=f"edition.finalize_from_session `{nid}`")

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
        pl["result_edition_forked"] = True
        pl["result_edition_forked_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        pl["source_edition_id"] = src
        bind_out: dict[str, Any] = {}
        if fp:
            try:
                bind_out = client.bind_book_edition(
                    fp, nid, source_job_id=job_id, note="finalize_from_session"
                )
                pl["bound_edition_id"] = nid
            except Exception as e:  # noqa: BLE001
                bind_out = {"error": str(e)}
        ctx.store.update_session(ctx.session_id, payload=pl)
        from ...next_steps import next_steps_message

        bind_note = ""
        if fp and bind_out and not bind_out.get("error"):
            bind_note = f"已绑定书指纹→专书方案 `{nid}`。"
        elif bind_out.get("error"):
            bind_note = f"专书绑定未成功：{bind_out.get('error')}。"
        msg = next_steps_message(
            pl,
            preface=(
                f"已新建排版方案草稿 `{nid}`（源自 `{src}`），"
                f"写入 layout_change_log={len(slim_log)} 条、"
                f"effective_notes={len(changes)} 条。"
                f"{bind_note}"
                "说明：待审议队列里的条目仍挂在各自 enqueue 时的方案 id；"
                "本草稿主要沉淀账本/策略。请打开方案页核对改进点。"
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
            "book_bind": bind_out,
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

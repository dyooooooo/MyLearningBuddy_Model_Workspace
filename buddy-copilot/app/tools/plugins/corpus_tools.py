"""语料导入工具插件（MinerU 线）：任务查询、发起导入、单页重处理。"""

from __future__ import annotations

import time
from typing import Any

from ..registry import ToolContext, ToolSpec, register


def _trim(obj: Any, limit: int = 1500) -> str:
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + f"...(截断，共{len(s)}字符)"


_NO_EFFECT_HINT = (
    "IR 已写盘，但该块/页渲染后的 HTML 与改动前一致——本次修改没有产生效果。"
    "不要重复同一个 patch：先看 rendered_snippet 判断是哪个字段决定渲染，"
    "必要时改用 corpus.replace_page_blocks；仍无效就把它写进 issues 交人工，别继续试。"
)


def _write_verdict(
    out: dict[str, Any],
    *,
    block_id: str = "",
    touched_text: bool = False,
) -> dict[str, Any]:
    """把 buddy-api 的写盘结果翻译成「这次改动到底生效了没」。

    省掉 Agent 「改 → 重渲 → 读页 → 比对」的四次调用：写盘工具自己回答。
    """
    changed = bool(out.get("html_changed"))
    verdict: dict[str, Any] = {"html_changed": changed}

    snippet = out.get("block_html")
    if snippet:
        verdict["rendered_snippet"] = _trim(snippet, 800)

    dropped = out.get("dropped_stale_meta")
    if dropped:
        verdict["invalidated_stale_meta"] = dropped

    # 改了 text 却仍留着 text_segments：渲染仍走 segments，text 不会显示出来
    if touched_text and block_id:
        for b in out.get("blocks") or []:
            if b.get("id") != block_id:
                continue
            if (b.get("meta") or {}).get("text_segments"):
                verdict["shadowed_by"] = ["meta.text_segments"]
                verdict["shadow_hint"] = (
                    "该块仍带 meta.text_segments，渲染优先用它而不是 text。"
                    "要让 text 生效，请在 patch 的 meta 里给出新 text_segments，或不要显式传它。"
                )
            break

    if not changed:
        verdict["hint"] = _NO_EFFECT_HINT
    return verdict


@register
def build(ctx: ToolContext) -> list[ToolSpec]:
    client = ctx.client

    def _session_inherit_job_id() -> str:
        """导入面板选定的「继承上次结果」来源任务（无则空=重新生成）。"""
        if not ctx.session_id:
            return ""
        try:
            sess = ctx.store.get_session(ctx.session_id) or {}
            pl = sess.get("payload") or {}
            return str(pl.get("inherit_from_job_id") or "").strip()
        except Exception:  # noqa: BLE001 - 拿不到就按重新生成走
            return ""

    def _emit_import(event: str, **fields: Any) -> None:
        """落库 import_event（前端打开导入面板）+ 关键节点的中文 system 明示。"""
        if not ctx.session_id:
            return
        line = str(fields.get("line") or "mineru")
        payload = {"event": event, "line": line, **fields}
        try:
            ctx.store.add_message(ctx.session_id, "import_event", payload)
            # 会话 payload 记下最近 job，供结果卡按钮使用
            sess = ctx.store.get_session(ctx.session_id) or {}
            pl = dict(sess.get("payload") or {})
            jid = str(fields.get("job_id") or "")
            bound = str(pl.get("import_job_id") or pl.get("job_id") or "")
            # 只在「首次绑定」或「同一任务」时写回；
            # 碰到别的任务不静默改绑（否则一会话会飘到另一本书的任务上）
            if jid and (not bound or bound == jid):
                pl["import_job_id"] = jid
                pl["job_id"] = jid
                pl["import_line"] = line
                ctx.store.update_session(ctx.session_id, payload=pl)
                try:
                    ctx.store.bind_import_job(ctx.session_id, jid)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

        job_id = fields.get("job_id") or "?"
        status = fields.get("status") or ""
        pages_ready = fields.get("pages_ready")
        page_limit = fields.get("page_limit")
        edition_id = fields.get("edition_id") or ""
        err = fields.get("error")

        if event == "import_started":
            lim = f"前 {page_limit} 页" if page_limit else "全书（未设 page_limit）"
            text = (
                f"📤 已发起预览版导入（MinerU 线）\n"
                f"· 任务 job_id=`{job_id}`\n"
                f"· 范围：{lim}"
                + (f" · 方案 `{edition_id}`" if edition_id else "")
                + "\n→ 请在导入面板查看进度与逐页预览。"
            )
            ctx.store.add_message(ctx.session_id, "system", {"text": text})
        elif event == "import_progress":
            # 进度只走 import_event，避免刷屏；前端更新 live 行即可
            return
        elif event == "import_done":
            ready = f"，pages_ready={pages_ready}" if pages_ready is not None else ""
            text = (
                f"✅ 已导入完成（预览版）\n"
                f"· 任务 job_id=`{job_id}` · 状态 `{status}`{ready}\n"
                f"→ 已可在导入界面逐页查看；偏差页按 page_plan 的 action 重处理。"
            )
            ctx.store.add_message(ctx.session_id, "system", {"text": text})
        elif event == "import_failed":
            text = (
                f"❌ 导入未完成\n"
                f"· 任务 job_id=`{job_id}` · 状态 `{status}`\n"
                f"· 原因：{err or '未知'}\n"
                f"→ 仍可在导入面板打开该任务查看日志。"
            )
            ctx.store.add_message(ctx.session_id, "system", {"text": text})

    def corpus_list_jobs() -> Any:
        """列出语料导入任务（MinerU 线）。"""
        return client.list_import_jobs()

    def corpus_job(job_id: str) -> Any:
        """取导入任务状态与元数据（含 edition_id、pipeline_phase、pages_ready）。"""
        return client.get_import_job(job_id)

    def corpus_page_context(job_id: str, page_no: int, block_id: str = "") -> Any:
        """取某页重识别上下文：中间表示（IR）块、profile、指纹、可用 stage 工件。"""
        return client.get_rerecognize_context(job_id, int(page_no), block_id or None)

    def corpus_page_html(job_id: str, page_no: int) -> Any:
        """取某页当前渲染 HTML（草稿 chunk 优先）。"""
        return client.get_page_html(job_id, int(page_no))

    def _wait_job(
        job_id: str,
        *,
        edition_id: str,
        page_limit: int | None,
        page_ranges: str | None,
        wait_timeout: int,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + float(wait_timeout)
        last_sig = ""
        while time.monotonic() < deadline:
            job = client.get_import_job(str(job_id))
            status = str(job.get("status") or "")
            meta = job.get("meta") or {}
            pages_ready = meta.get("pages_ready")
            result["status"] = status
            result["pages_ready"] = pages_ready
            sig = f"{status}:{pages_ready}:{meta.get('pipeline_phase')}"
            if sig != last_sig:
                last_sig = sig
                _emit_import(
                    "import_progress",
                    job_id=job_id,
                    status=status,
                    pages_ready=pages_ready,
                    pipeline_phase=meta.get("pipeline_phase"),
                    page_limit=page_limit,
                    page_ranges=page_ranges,
                    edition_id=edition_id,
                )
            if status == "failed":
                result["error"] = _trim(job.get("error"))
                _emit_import(
                    "import_failed",
                    job_id=job_id,
                    status=status,
                    error=result["error"],
                    page_limit=page_limit,
                    page_ranges=page_ranges,
                    edition_id=edition_id,
                )
                break
            from ...segment_state import import_batch_ready

            if import_batch_ready(job, page_ranges=page_ranges, page_limit=page_limit):
                _emit_import(
                    "import_done",
                    job_id=job_id,
                    status=status,
                    pages_ready=pages_ready,
                    page_limit=page_limit,
                    page_ranges=page_ranges,
                    edition_id=edition_id,
                    ok=True,
                )
                break
            time.sleep(10)
        else:
            _emit_import(
                "import_failed",
                job_id=job_id,
                status=result.get("status") or "timeout",
                error=f"等待样页超时（{wait_timeout}s），任务可能仍在后台跑",
                page_limit=page_limit,
                page_ranges=page_ranges,
                edition_id=edition_id,
            )
        return result

    def corpus_start_import(
        minio_key: str,
        edition_id: str,
        label: str = "",
        subject: str = "math",
        grade_band: str = "hs",
        page_limit: int = 0,
        page_ranges: str = "",
        wait: bool = False,
        wait_timeout: int = 1800,
    ) -> Any:
        """用指定方案发起 PDF 导入（minio_key 必须是已存在的真实对象键，可先用
        minio.stat 预检）。page_ranges 如 "1-20" 优先于 page_limit。
        wait=true 时阻塞等样页就绪。会向会话推送 import_event。"""
        lim = int(page_limit) if page_limit else 0
        pr = (page_ranges or "").strip() or None
        # 「继承上次结果」由用户在导入面板选定，写在会话 payload 里；
        # 由工具自己带上，不依赖模型记得传参。
        inherit = _session_inherit_job_id()
        out = client.start_pdf_import(
            minio_key=minio_key,
            edition_id=edition_id,
            label=label,
            subject=subject,
            grade_band=grade_band,
            page_limit=None if pr else (lim or None),
            page_ranges=pr,
            inherit_from_job_id=inherit,
        )
        job_id = out.get("id")
        result: dict[str, Any] = {
            "job_id": job_id,
            "status": out.get("status"),
            "page_limit": None if pr else (lim or None),
            "page_ranges": pr,
            "edition_id": edition_id,
            "line": "mineru",
            "preview_import": True,
        }
        if inherit:
            result["inherit_from_job_id"] = inherit
        _emit_import(
            "import_started",
            job_id=job_id,
            status=out.get("status"),
            page_limit=result["page_limit"],
            page_ranges=pr,
            edition_id=edition_id,
            minio_key=minio_key,
        )
        if wait and job_id:
            _wait_job(
                str(job_id),
                edition_id=edition_id,
                page_limit=result["page_limit"],
                page_ranges=pr,
                wait_timeout=wait_timeout,
                result=result,
            )
        return result

    # Copilot 仅 import_advice：禁止 35B refine/rebuild；改页用 patch/replace/rerender
    _REFINE_35B_MODES = frozenset(
        {"content_correct", "layout_content", "layout_rebuild", "refine_35b"}
    )
    _IMPORT_ADVICE_OK_MODES = frozenset(
        {
            "rerender",
            "from_blocks",
            "restore_mineru",
            "mineru_rerecognize",
            "from_cloud",
        }
    )

    def _record_layout_change(
        *,
        op: str,
        page: int | None,
        summary: str,
        before_hint: str = "",
        after_hint: str = "",
        block_ids: list[str] | None = None,
    ) -> str | None:
        """工具层强制记账；返回 layout log id。"""
        if not ctx.session_id:
            return None
        try:
            from ...segment_state import append_layout_change

            sess = ctx.store.get_session(ctx.session_id) or {}
            pl = dict(sess.get("payload") or {})
            from ...authority import stamp_authority

            bids = [str(b) for b in (block_ids or []) if str(b).strip()]
            entry = stamp_authority(
                {
                    "op": op,
                    "tool": f"corpus.{op}" if not str(op).startswith("corpus.") else op,
                    "page": page,
                    "summary": summary,
                    "args_summary": summary[:300],
                    "before_hint": before_hint,
                    "after_hint": after_hint,
                    "block_ids": bids,
                    "proposer": "ai",
                    "mode": op,
                },
                mode=op,
            )
            pl["layout_change_log"] = append_layout_change(pl, entry)
            # Sync attempts to buddy-api IP store (must be visible on failure)
            ingest_err = ""
            job_id = str(pl.get("import_job_id") or pl.get("job_id") or "").strip()
            fp = ""
            bf = pl.get("book_fingerprint") or {}
            if isinstance(bf, dict):
                fp = str(bf.get("hash") or "")
            log_tail = (pl.get("layout_change_log") or [])[-1:]
            if job_id and log_tail:
                try:
                    client.ip_ingest_attempts(
                        {
                            "attempts": log_tail,
                            "job_id": job_id,
                            "session_id": ctx.session_id or "",
                            "fingerprint_hash": fp,
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    ingest_err = str(e)[:240]
                    pl["last_ip_ingest_error"] = ingest_err
            ctx.store.update_session(ctx.session_id, payload=pl)
            log = pl.get("layout_change_log") or []
            aid = str(log[-1].get("id")) if log else None
            if ingest_err and aid:
                # Keep attempt id but surface ingest failure to tool callers
                entry["ip_ingest_error"] = ingest_err
            return aid
        except Exception:  # noqa: BLE001
            return None

    def _session_edition_id() -> str:
        if not ctx.session_id:
            return ""
        try:
            sess = ctx.store.get_session(ctx.session_id) or {}
            pl = sess.get("payload") or {}
            # Prefer fork result so「待审议」落在用户看到的那本草稿上
            return str(
                pl.get("result_edition_id")
                or pl.get("edition_id")
                or pl.get("source_edition_id")
                or ""
            ).strip()
        except Exception:  # noqa: BLE001
            return ""

    def _emit_page_refresh(
        job_id: str,
        page_no: int,
        note: str,
        *,
        mode: str = "cursor_ir",
        ok: bool = True,
        html_changed: bool | None = None,
        block_id: str = "",
        summary: str = "",
        promote_prompt: bool | None = None,
    ) -> None:
        """通知前端刷预览；改动写盘后附带「是否记入待审议」提示字段。"""
        if not ctx.session_id:
            return
        # 纯重渲不算「新改进」；IR 写盘成功即使 html 未变也要提示记入待审议
        if promote_prompt is None:
            if mode == "rerender":
                promote_prompt = False
            else:
                promote_prompt = bool(ok)
        eid = _session_edition_id()
        from ...authority import authority_label, classify_authority

        auth = classify_authority(mode=mode)
        try:
            payload: dict[str, Any] = {
                "event": "reprocess_done",
                "line": "mineru",
                "job_id": job_id,
                "page_no": int(page_no),
                "mode": mode,
                "ok": bool(ok),
                "promote_prompt": bool(promote_prompt),
                "summary": (summary or note or "")[:400],
                "authority": auth,
                "authority_label": authority_label(auth),
            }
            if eid:
                payload["edition_id"] = eid
            if html_changed is not None:
                payload["html_changed"] = bool(html_changed)
            if block_id:
                payload["block_id"] = block_id
            ctx.store.add_message(ctx.session_id, "import_event", payload)
            ctx.store.add_message(ctx.session_id, "system", {"text": note})
        except Exception:  # noqa: BLE001
            pass

    def corpus_get_blocks(job_id: str, page_no: int = 0) -> Any:
        """读取导入任务中间表示（IR）块；page_no>0 时只返回该页。"""
        out = client.get_job_blocks(job_id)
        blocks = list(out.get("blocks") or [])
        pn = int(page_no or 0)
        if pn > 0:
            blocks = [b for b in blocks if int(b.get("page_no") or 0) == pn]
        slim = []
        for b in blocks[:200]:
            meta = b.get("meta") or {}
            slim.append(
                {
                    "id": b.get("id"),
                    "type": b.get("type"),
                    "page_no": b.get("page_no"),
                    "order": b.get("order"),
                    "text": _trim(b.get("text") or "", 240),
                    "caption": _trim(b.get("caption") or "", 120),
                    "layout_zone": meta.get("layout_zone"),
                    "pedagogy_role": meta.get("pedagogy_role"),
                    "reading_order": meta.get("reading_order"),
                    # A 层 provenance：与 layout_escalate / page-diagnosis 对齐
                    "layout_rule_id": meta.get("layout_rule_id"),
                    "layout_confidence": meta.get("layout_confidence"),
                    "layout_confidence_score": meta.get("layout_confidence_score"),
                    "layout_evidence": meta.get("layout_evidence"),
                    "layout_escalate": meta.get("layout_escalate"),
                    "bbox": b.get("bbox"),
                    # 该块 HTML 由谁决定：有 text_segments 时改 text 需连带刷新，
                    # patch_block 会自动作废这份缓存
                    "renders_from": (
                        "text_segments" if meta.get("text_segments") else "text"
                    ),
                }
            )
        return {
            "job_id": job_id,
            "page_no": pn or None,
            "block_count": len(blocks),
            "blocks": slim,
            "truncated": len(blocks) > 200,
        }

    def corpus_patch_block(
        job_id: str,
        block_id: str,
        patch_json: str = "{}",
        page_no: int = 0,
        summary: str = "",
    ) -> Any:
        """单块改写 IR（可改 text/caption/layout_zone/pedagogy_role/order/meta）。

        返回 html_changed + rendered_snippet：改动是否真的进了 HTML，一次调用即可验收，
        不需要再 rerender_page + page_html 对比。改 text 时会自动作废陈旧的
        meta.text_segments 缓存。成功后自动记入 layout_change_log。"""
        import json

        try:
            patch = json.loads(patch_json or "{}")
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"patch_json 非法: {e}"}
        if not isinstance(patch, dict) or not patch:
            return {"ok": False, "error": "patch_json 须为非空对象"}
        before = ""
        try:
            all_b = client.get_job_blocks(job_id).get("blocks") or []
            for b in all_b:
                if b.get("id") == block_id:
                    before = _trim(
                        f"order={b.get('order')} zone={(b.get('meta') or {}).get('layout_zone')} "
                        f"text={b.get('text') or ''}",
                        200,
                    )
                    if not page_no:
                        page_no = int(b.get("page_no") or 0)
                    break
        except Exception:  # noqa: BLE001
            pass
        out = client.patch_job_block(job_id, block_id, patch)
        after = _trim(str(patch), 200)
        verdict = _write_verdict(out, block_id=block_id, touched_text="text" in patch)
        log_id = _record_layout_change(
            op="patch_block",
            page=int(page_no) if page_no else None,
            summary=summary or f"patch block {block_id}",
            before_hint=before,
            after_hint=after,
            block_ids=[block_id],
        )
        if page_no:
            _emit_page_refresh(
                job_id,
                int(page_no),
                f"✅ 已改 IR 块 `{block_id}`（页 {page_no}）· job=`{job_id}`。"
                + (
                    " 页面 HTML 已变化（见 html_changed），一般无需再 rerender。"
                    if verdict.get("html_changed")
                    else " 页面 HTML 未变化——看 rendered_snippet / hint，勿空转重渲。"
                ),
                mode="cursor_ir",
                ok=True,
                html_changed=bool(verdict.get("html_changed")),
                block_id=block_id,
                summary=summary or f"patch block {block_id}",
            )
        return {
            "ok": True,
            "job_id": job_id,
            "block_id": block_id,
            "block_count": out.get("block_count"),
            "layout_log_id": log_id,
            **verdict,
        }

    def corpus_replace_page_blocks(
        job_id: str,
        page_no: int,
        blocks_json: str,
        summary: str = "",
        rerender: bool = True,
    ) -> Any:
        """整页重排/重写 IR：blocks_json 为该页块数组（保留 id，顺序即阅读序）。

        默认写盘后 rerender，并返回 html_changed 供直接验收。改 text 的块会自动
        作废陈旧 meta.text_segments。成功自动记 layout_change_log。"""
        import json

        try:
            blocks = json.loads(blocks_json or "[]")
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"blocks_json 非法: {e}"}
        if not isinstance(blocks, list) or not blocks:
            return {"ok": False, "error": "blocks_json 须为非空数组"}
        before_ids: list[str] = []
        try:
            all_b = client.get_job_blocks(job_id).get("blocks") or []
            before_ids = [
                str(b.get("id"))
                for b in all_b
                if int(b.get("page_no") or 0) == int(page_no)
            ]
        except Exception:  # noqa: BLE001
            pass
        out = client.replace_page_blocks(
            job_id, int(page_no), blocks, rerender=bool(rerender)
        )
        after_ids = [str(b.get("id")) for b in (out.get("blocks") or []) if b.get("id")]
        log_id = _record_layout_change(
            op="replace_page_blocks",
            page=int(page_no),
            summary=summary or f"replace page {page_no} layout ({len(blocks)} blocks)",
            before_hint=f"ids={before_ids[:40]}",
            after_hint=f"ids={after_ids[:40]}",
            block_ids=after_ids[:80],
        )
        verdict = _write_verdict(out)
        _emit_page_refresh(
            job_id,
            int(page_no),
            f"✅ 页 {page_no} 排版已由 Cursor 重写（{len(blocks)} 块）· job=`{job_id}`"
            + (" · 已 rerender" if out.get("rerendered") else "")
            + " → 请 preview+Read 验收。",
            mode="cursor_ir",
            ok=True,
            html_changed=bool(verdict.get("html_changed")),
            summary=summary or f"replace page {page_no} layout ({len(blocks)} blocks)",
        )
        return {
            "ok": True,
            "job_id": job_id,
            "page_no": int(page_no),
            "block_count": out.get("block_count"),
            "rerendered": out.get("rerendered"),
            "layout_log_id": log_id,
            "html_chars": len(out.get("html") or "") if out.get("html") else 0,
            **verdict,
        }

    def corpus_rerender_page(job_id: str, page_no: int) -> Any:
        """仅从已存 IR 重编译页 HTML（无 MinerU、无 35B）。"""
        out = client.rerender_page(job_id, int(page_no))
        _emit_page_refresh(
            job_id,
            int(page_no),
            f"✅ 页 {page_no} 已 rerender · job=`{job_id}`。",
            mode="rerender",
            ok=True,
            promote_prompt=False,
        )
        return {
            "ok": True,
            "job_id": job_id,
            "page_no": int(page_no),
            "html_chars": len(out.get("html") or ""),
        }

    def corpus_rebuild_ir(job_id: str, pages_json: str = "") -> Any:
        """Layout-only 重跑 A 层（math→规则→HTML），合并更新 layout_escalate_*。

        改完 layout_rules / page override 后调用。pages_json 为 JSON 数组如
        ``[3,10]``；空则全书。**无 35B**。"""
        import json

        pages: list[int] | None = None
        raw = (pages_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as e:
                return {"ok": False, "error": f"pages_json 非法: {e}"}
            if not isinstance(parsed, list):
                return {"ok": False, "error": "pages_json 须为页号数组"}
            pages = [int(p) for p in parsed]
        out = client.rebuild_ir(job_id, pages=pages)
        note = (
            f"✅ layout-only rebuild_ir · job=`{job_id}`"
            + (f" · pages={pages}" if pages else " · 全书")
        )
        # escalate 页数是全书回归闸：规则全书生效，只看改动页会漏掉被弄坏的页。
        esc = (out or {}).get("escalate") or {}
        verdict = ""
        if esc:
            delta = int(esc.get("delta") or 0)
            if delta > 0:
                verdict = f"⚠️ escalate 增加 {delta} 页（新增 {esc.get('newly_escalated')}）—— 规则弄坏了别的页，不要落地"
            elif delta < 0:
                verdict = f"✅ escalate 减少 {-delta} 页，无新增回归"
            else:
                verdict = "✅ escalate 持平，无新增回归"
            note += f" · {verdict}"
        try:
            if ctx.session_id:
                ctx.store.add_message(ctx.session_id, "system", {"text": note})
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": True,
            "job_id": job_id,
            "pages": pages or "all",
            "regression_verdict": verdict,
            **(out or {}),
        }

    def corpus_reprocess_page(
        job_id: str, page_no: int, mode: str = "layout_content", user_note: str = ""
    ) -> Any:
        """单页重处理。仅允许 rerender / restore_mineru / from_cloud（无 35B）；
        排版/内容修正请用 corpus.replace_page_blocks / patch_block。"""
        mode_s = (mode or "").strip() or "layout_content"
        if mode_s in _REFINE_35B_MODES:
            return {
                "ok": False,
                "error": (
                    "禁用 35B reprocess "
                    f"（mode={mode_s}）。请用 corpus.get_blocks + "
                    "corpus.replace_page_blocks / corpus.patch_block + "
                    "corpus.rerender_page 由 Cursor 直接改中间表示（IR）。"
                ),
                "mode": mode_s,
                "allowed_modes": sorted(_IMPORT_ADVICE_OK_MODES),
            }
        out = client.reprocess_page(job_id, int(page_no), mode_s, user_note)
        keep = {
            k: out.get(k)
            for k in (
                "ok", "job_id", "page_no", "mode", "persisted", "figure_count",
                "finding_count", "noop", "used_35b", "used_mineru", "judge_summary",
            )
        }
        keep["findings"] = _trim(out.get("findings"))
        if ctx.session_id and job_id:
            note = (
                f"{'✅' if out.get('ok') else '⚠️'} 页 {page_no} 已重处理（mode={mode_s}）· "
                f"job_id=`{job_id}` → 导入预览已刷新该页。"
            )
            _emit_page_refresh(
                job_id,
                int(page_no),
                note,
                mode=mode_s,
                ok=bool(out.get("ok")),
                html_changed=None if out.get("noop") is None else (not bool(out.get("noop"))),
                summary=user_note or f"reprocess mode={mode_s}",
                promote_prompt=bool(out.get("ok")) and not bool(out.get("noop")),
            )
        return keep

    return [
        ToolSpec("corpus.list_jobs", "导入任务列表", corpus_list_jobs),
        ToolSpec("corpus.job", "导入任务详情", corpus_job),
        ToolSpec("corpus.page_context", "页重识别上下文", corpus_page_context),
        ToolSpec("corpus.page_html", "页渲染 HTML", corpus_page_html),
        ToolSpec("corpus.start_import", "发起 PDF 导入", corpus_start_import),
        ToolSpec("corpus.get_blocks", "读 IR 块", corpus_get_blocks),
        ToolSpec("corpus.patch_block", "单块改 IR", corpus_patch_block),
        ToolSpec("corpus.replace_page_blocks", "整页重排/重写 IR", corpus_replace_page_blocks),
        ToolSpec("corpus.rerender_page", "IR 重渲 HTML", corpus_rerender_page),
        ToolSpec(
            "corpus.rebuild_ir",
            "layout-only 重跑规则并刷新 escalate",
            corpus_rebuild_ir,
        ),
        ToolSpec("corpus.reprocess_page", "单页重处理", corpus_reprocess_page),
    ]

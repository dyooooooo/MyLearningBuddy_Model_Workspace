"""场景插件（OCP 扩展点）：kind → 上下文构建 + 任务使命 + 验收要求。

- 每个场景一个 Scenario 声明，注册进 SCENARIOS；新场景（如 ③ 诊断类任务）
  加一个 build 函数 + 注册一行即可，loop / mcp_server 不动。
- 场景可见的工具子集在 agent-env/permissions.yaml 的 scenarios 段声明。
- builder 可能会往 payload 里回写缓存（如 before_html），监督器负责持久化。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ..config import Settings
from ..tools.buddy_api import BuddyApiClient


def _j(obj: Any, limit: int = 6000) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + f"...(截断，共{len(s)}字符)"


@dataclass(frozen=True)
class Scenario:
    kind: str
    title: str
    build_context: Callable[[BuddyApiClient, dict[str, Any]], str]


SCENARIOS: dict[str, Scenario] = {}


def register_scenario(s: Scenario) -> Scenario:
    SCENARIOS[s.kind] = s
    return s


# ---------------- 场景 A：新 PDF 导入 → 整书方案建议 ----------------


def _import_advice_context(client: BuddyApiClient, payload: dict[str, Any]) -> str:
    parts: list[str] = ["### 场景 A：新 PDF 导入 → 整书方案建议\n"]
    parts.append("用户输入：\n```json\n" + _j(payload, 2000) + "\n```")

    try:
        overview = client.editions_overview()
        parts.append("现有方案清单（editions overview）：\n```json\n" + _j(overview) + "\n```")
    except Exception as e:  # noqa: BLE001
        parts.append(f"（editions overview 获取失败：{e}）")

    job_id = payload.get("job_id")
    if job_id:
        try:
            job = client.get_import_job(str(job_id))
            parts.append("已有导入任务元数据：\n```json\n" + _j(job, 3000) + "\n```")
            sample_pages = payload.get("sample_pages") or [1, 2, 3]
            bboxes: list[list[float]] = []
            for pn in sample_pages[:3]:
                try:
                    ctx = client.get_rerecognize_context(str(job_id), int(pn))
                    for b in (ctx.get("ir_blocks") or [])[:60]:
                        bb = b.get("bbox")
                        if isinstance(bb, list) and len(bb) == 4:
                            bboxes.append([float(x) for x in bb])
                except Exception:  # noqa: BLE001
                    continue
            if bboxes:
                payload["sample_bboxes"] = bboxes
                base = str(payload.get("edition_id") or job.get("meta", {}).get("edition_id") or "_base")
                try:
                    cal = client.calibrate(base, bboxes, persist_fork=False)
                    parts.append(
                        f"几何标定建议（calibrate，基于 {len(bboxes)} 个 bbox 样本）：\n```json\n"
                        + _j(cal, 4000) + "\n```"
                    )
                except Exception as e:  # noqa: BLE001
                    parts.append(f"（calibrate 调用失败：{e}）")
        except Exception as e:  # noqa: BLE001
            parts.append(f"（导入任务 {job_id} 获取失败：{e}）")
    else:
        staged = payload.get("minio_key")
        if staged:
            eid = str(payload.get("edition_id") or "").strip()
            parts.append(
                f"PDF 已预上传到 MinIO：`{staged}`"
                + (f"（约 {payload.get('page_count')} 页）" if payload.get("page_count") else "")
                + (f"；方案 `{eid}`" if eid else "；**须先选定 edition_id**")
                + "。标准动线（MinerU 优先分段）：① 编排器每批 **20 页** "
                "`corpus.start_import` / `corpus.extend_import`（page_ranges）→ "
                "② 生成最终 HTML，中栏可预览 → ③ 你用 `page_html`（必要时 "
                "`render_preview`+Read）**只看最终页面**列出问题 → "
                "④ **停下来等用户**：不修则下一批；用户点名修哪些页后才改 → "
                "⑤ 修改轮才用 `replace_page_blocks` / `patch_block`（自动记 "
                "layout_change_log）；确认有效再 `edition.record_effective_change`，"
                "收尾 `edition.finalize_from_session` fork 新排版方案草稿。\n"
                "质检轮**禁止**改页，也**禁止**把 MinerU 中间结构化块当依据。\n"
                "**禁止** layout.judge_35b / triad / compare，以及 reprocess 的 "
                "content_correct / layout_content / layout_rebuild（本场景不用 35B）。"
            )
        else:
            parts.append(
                "该 PDF 尚未导入且未预上传（payload 无 minio_key）。"
                "不要编造 minio_key；先与用户确认教材特征，比对现有方案给出建议。"
                "确需文件时，请用户在导入面板的「PDF 文件」里选择文件——前端会自动预上传并"
                "向你发一条『已预上传完成：minio_key=…』的消息，收到后直接用该 key 继续"
                "（minio.stat 预检 → corpus.render_preview 看版面 → corpus.start_import 导入），"
                "全程由你执行，不要再让用户手动导入。"
            )

    parts.append(
        "\n任务使命：MinerU 分段导入 → 你看最终页列问题 → 用户决定修不修：\n"
        "1. 编排器按 20 页批跑 MinerU 并生成最终 HTML；\n"
        "2. **质检轮**：只看 `page_html` / 渲染预览，列出 issues，**不要改页**；\n"
        "3. 用户「不修，下一批」→ 编排器前进；用户「按建议改」或「修第 X 页」→ "
        "进入修改轮，只改点名页；\n"
        "4. 合理请求在白名单内执行；违原则（改架构 / 动 35B / 越权）拒绝并说明；\n"
        "5. 确认有效 → edition.record_effective_change；跳过不算有效；\n"
        "6. 全书完成后 edition.finalize_from_session fork 草稿。\n"
        "\n改不动时的硬规则（修改轮才适用）：\n"
        "- 同一处改两次仍 html_changed=false → 停手，写进 issues 交用户；\n"
        "- 判断是渲染器/服务端代码缺陷 → fs.request_edit 申请或写进 issues，"
        "绝不直接改 app/ 源码；\n"
        "- 本场景没有 docker.rebuild：重建由人工执行。\n"
        "\n收尾契约：最终报告 JSON 含 job_id、import_line=\"mineru\"、"
        "result_edition_id（若有）、summary。\n"
        "质检验收依据：最终 HTML / 渲染效果（不是中间结构化文本）。"
    )
    return "\n\n".join(parts)


# ---------------- 场景 B：页级识别问题分诊 ----------------


def _page_assist_context(client: BuddyApiClient, payload: dict[str, Any]) -> str:
    parts: list[str] = ["### 场景 B：页级识别问题分诊\n"]
    job_id = str(payload.get("job_id") or "")
    page_no = int(payload.get("page_no") or 0)
    block_id = payload.get("block_id")
    user_note = str(payload.get("user_note") or "")

    parts.append(
        f"目标：job `{job_id}` 第 {page_no} 页"
        + (f"，块 `{block_id}`" if block_id else "")
        + f"\n用户诉求：{user_note or '（用户未填写，先在对话里问清楚）'}"
    )

    try:
        job = client.get_import_job(job_id)
        parts.append("导入任务元数据：\n```json\n" + _j(job, 2500) + "\n```")
        edition_id = str((job.get("meta") or {}).get("edition_id") or job.get("edition_id") or "")
        if edition_id:
            try:
                bundle = client.get_bundle(edition_id)
                slim = {
                    "meta": bundle.get("meta"),
                    "config": {
                        "refine_mode": (bundle.get("config") or {}).get("refine_mode"),
                        "geometry": (bundle.get("config") or {}).get("geometry"),
                        "features": (bundle.get("config") or {}).get("features"),
                    },
                    "policy_entry_count": len((bundle.get("policy") or {}).get("entries") or []),
                }
                parts.append(f"当前方案 `{edition_id}` 概要：\n```json\n" + _j(slim, 3000) + "\n```")
            except Exception as e:  # noqa: BLE001
                parts.append(f"（方案 bundle 获取失败：{e}）")
    except Exception as e:  # noqa: BLE001
        parts.append(f"（导入任务获取失败：{e}）")

    try:
        ctx = client.get_rerecognize_context(job_id, page_no, str(block_id) if block_id else None)
        slim_ctx = {
            "profile": ctx.get("profile"),
            "parse_engine": ctx.get("parse_engine"),
            "scope": ctx.get("scope"),
            "ir_block_count": len(ctx.get("ir_blocks") or []),
            "ir_blocks_head": (ctx.get("ir_blocks") or [])[:20],
            "stages": list((ctx.get("stages") or {}).keys()),
        }
        parts.append("页上下文（rerecognize-context 精简）：\n```json\n" + _j(slim_ctx, 6000) + "\n```")
    except Exception as e:  # noqa: BLE001
        parts.append(f"（页上下文获取失败：{e}）")

    # 缓存 before HTML，供 harness.page_diff 做前后对比
    try:
        before = str(client.get_page_html(job_id, page_no))
        payload["before_html"] = before
        parts.append(f"（已缓存当前页 HTML 作为前后对比基线，{len(before)} 字符）")
    except Exception as e:  # noqa: BLE001
        parts.append(f"（页 HTML 获取失败：{e}）")

    parts.append(
        "\n任务使命：分诊到四条通道之一并亲自执行——"
        "① 单页问题 → corpus.reprocess_page（mode: content_correct / layout_content / layout_rebuild，带 user_note）；"
        "② 系统性版式问题 → editions.save_draft 改方案草稿（geometry/patterns/policy）后对该页重跑；"
        "③ Prompt 槽位问题 → fs.request_edit 申请改 prompt 模板 → 编辑 → docker.rebuild（昂贵通道）；"
        "④ 代码缺陷 → fs.request_edit 申请 → 编辑 → harness.run_pytest + docker.rebuild（昂贵通道）。"
        "\n验收要求：最终报告 evidence 必须包含 harness.page_diff（job_id + page_no）结果；"
        "昂贵通道还须 pytest / golden 通过。"
    )
    return "\n\n".join(parts)


register_scenario(Scenario("import_advice", "新 PDF 导入方案建议", _import_advice_context))
register_scenario(Scenario("page_assist", "页级识别问题分诊", _page_assist_context))


def build_task_context(settings: Settings) -> Callable[[str, dict[str, Any]], str]:
    client = BuddyApiClient(settings)

    def builder(kind: str, payload: dict[str, Any]) -> str:
        scenario = SCENARIOS.get(kind)
        if scenario is None:
            raise ValueError(f"未知场景: {kind}")
        return scenario.build_context(client, payload)

    return builder

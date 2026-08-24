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


def _symptom_context(client: BuddyApiClient, job_id: str) -> str:
    """已知症状（有成功路径）与未知疑点（需诊断）分开给，别混成一锅。"""
    from ..segment_state import symptom_hints_instruction

    try:
        data = client.job_symptoms(job_id)
    except Exception as e:  # noqa: BLE001
        return f"（症状清单获取失败：{e}）"
    block = symptom_hints_instruction(data)
    if not block:
        return "本次导入没有命名症状命中（`symptom_findings` 为空）。"
    return (
        f"覆盖情况 `{data.get('coverage') or 'none'}`。命名症状与上面的低置信页清单"
        "**并列**：一个是命名症状，一个是数值兜底，别混着看。\n\n" + block
    )


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
            job_meta = job.get("meta") if isinstance(job.get("meta"), dict) else {}
            esc = job_meta.get("layout_escalate_pages") or []
            if esc:
                parts.append(
                    f"**低置信页待 Cursor 判定**（`layout_escalate_pages`，共 {len(esc)} 页）："
                    f"{esc[:40]}{'…' if len(esc) > 40 else ''}。"
                    "优先按 `skills/page-diagnosis.md` 通道 0 处理；"
                    "**禁止**默认 35B refine。\n"
                    "摘要：\n```json\n"
                    + _j(job_meta.get("layout_escalate_summary") or {}, 2000)
                    + "\n```"
                )
            parts.append(_symptom_context(client, str(job_id)))
            sample_pages = payload.get("sample_pages") or list(esc[:3]) or [1, 2, 3]
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
                + "。标准动线（MinerU 全书一次 + Cursor 自动改）："
                "① 编排器 `corpus.start_import` **不传** page_ranges / page_limit（全书）→ "
                "worker 跑几何+规则生成最终 HTML，并写入两份**并列**的疑点清单："
                "`symptom_findings` / `symptom_summary`（命名症状，有名字有处置办法）"
                "与 `layout_escalate_*`（低置信页，数值兜底）→ "
                "② 分诊**先命名症状后 escalate**：`symptoms.for_job` 取命名症状"
                "（`corpus.job` 的 meta 里也有同样两个字段），再读 escalate 清单；"
                "两条都用 `page_html` / `get_blocks`"
                "（含 layout_rule_id / layout_confidence_score）对照最终页面 → "
                "③ **直接自动改**（勿等用户确认）：`layout_rules.yaml` / "
                "`overrides/pages/NNNN.yaml`（四位页号，经 `fs.request_edit` 批准后写）/ "
                "`patch_block` / `replace_page_blocks` / `rerender_page`；"
                "改完规则或 override 用 `corpus.rebuild_ir(job_id, pages_json=\"[12,34]\")` "
                "重跑（该工具恒为 layout-only，没有也不要传 layout_only 参数；"
                "pages_json 留空＝全书）→ "
                "④ `edition.record_effective_change`（写入 accepted IP+procedure；"
                "中间无效尝试不算有效）；收尾前 converge，再 "
                "`edition.finalize_from_session`（绑定专书方案）。\n"
                "**禁止** layout.judge_35b / triad / compare，以及 reprocess 的 "
                "content_correct / layout_content / layout_rebuild / refine_35b。"
            )
            inherit_from = str(payload.get("inherit_from_job_id") or "").strip()
            if inherit_from:
                parts.append(
                    f"**同一本书重导 · 用户选了「继承上次结果」**（来源任务 `{inherit_from}`）。"
                    "start_import 会自动带上该来源（你无需传参）：worker 解析完会把上次已改好的页"
                    "整页搬过来，并把这些页从 `layout_escalate_pages` 里摘掉。"
                    "所以你**只处理剩下的 escalate 页**，不要重做已继承的页；"
                    "任务 meta 的 `inherit_result` 里有继承页清单与跳过原因，"
                    "开工前先读一遍（`corpus.job`）。"
                )
        else:
            parts.append(
                "该 PDF 尚未导入且未预上传（payload 无 minio_key）。"
                "不要编造 minio_key；先与用户确认教材特征，比对现有方案给出建议。"
                "确需文件时，请用户在导入面板的「PDF 文件」里选择文件——前端会自动预上传并"
                "向你发一条『已预上传完成：minio_key=…』的消息，收到后直接用该 key 继续"
                "（minio.stat 预检 → corpus.render_preview 看版面 → corpus.start_import 导入），"
                "全程由你执行，不要再让用户手动导入。"
                "minio.stat 返回 `checked=false` 只说明预检没跑成，**不代表对象不存在**，"
                "照常继续导入，不要因此停下来问用户。"
            )

    parts.append(
        "\n任务使命：MinerU 全书一次导入 → Cursor 按疑点清单自动改 → 沉淀方案：\n"
        "1. 编排器 `corpus.start_import` **不传** page_ranges / page_limit（全书）；\n"
        "2. Worker 跑几何+规则写出最终 HTML，并确定性写出两份**并列**清单："
        "命名症状 `symptom_findings` / `symptom_summary` 与低置信页 `layout_escalate_*`；\n"
        "3. 分诊**先命名症状**（已经在别的书上修好过，照 `rule` 档的 landing_target 或 "
        "`playbook` 档的步骤做）**再 escalate**（还没有名字的数值兜底）；"
        "用 `page_html` / `get_blocks` 对照后 **直接改**"
        "（规则 YAML / page override / patch / replace / rerender / rebuild_ir），"
        "**不要**停下来问「按建议改吗」；\n"
        "4. 有效改动 → edition.record_effective_change；收尾 "
        "edition.finalize_from_session fork 草稿；\n"
        "5. 事后用户可再逐页聊（「修第 X 页」/「按建议改」/跳过遗留）；\n"
        "6. 合理请求在白名单内执行；违原则（改架构 / 动 35B / 越权）拒绝并说明。\n"
        "\n改不动时的硬规则：\n"
        "- 同一处改两次仍 html_changed=false → 停手，写进 issues；\n"
        "- 判断是渲染器/服务端代码缺陷 → fs.request_edit 申请或写进 issues，"
        "绝不直接改 app/ 源码；\n"
        "- 本场景没有 docker.rebuild：重建由人工执行。\n"
        "\n收尾契约：最终报告 JSON 含 job_id、import_line=\"mineru\"、"
        "result_edition_id（若有）、summary。\n"
        "验收依据：最终 HTML / 渲染效果（不是中间结构化文本）。"
    )
    return "\n\n".join(parts)


register_scenario(Scenario("import_advice", "新 PDF 导入方案建议", _import_advice_context))


# ---------------- 场景 B：改进点抽象 → 判定生效面并落地 ----------------


def _abstract_source_ip(row: dict[str, Any]) -> dict[str, Any]:
    """一条待抽象改进点的原料视图。

    **不给摘要全文**。摘要是施工记录，掺着页码和具体正文，照着它归类只会抽出
    「第 12 页那张图」这种换本书就没用的东西。归症状要看的是用了什么工具、改了
    什么位置这些结构信息。
    """
    return {
        "ip_id": row.get("id"),
        "page_no": row.get("page_no"),
        "category": row.get("category"),
        "kind": row.get("kind"),
        "locus_key": row.get("locus_key"),
        "tools": row.get("tools") or [],
        "summary_hint": str(row.get("summary") or "")[:80],
    }


def _pattern_abstract_context(client: BuddyApiClient, payload: dict[str, Any]) -> str:
    parts: list[str] = ["### 场景 B：改进点抽象 → 症状 · 检测器 · 处置 · 生效面\n"]
    parts.append("用户输入：\n```json\n" + _j(payload, 1500) + "\n```")

    fp = str(payload.get("fingerprint_hash") or "").strip()
    batch = int(payload.get("batch_size") or 25)

    # 先给原料。以前这里只给模式列表，而模式桶全是空的 —— 等于让 Agent 对着空表
    # 干活，抽不出东西来是必然的。
    try:
        listing = client.ip_list(
            fingerprint_hash=fp, status="accepted", prep_status="ready", limit=1000
        )
        items = listing.get("items") or listing.get("improvement_points") or []
        unassigned = [r for r in items if not (r.get("pattern_id") or "")]
        parts.append(
            f"**待抽象改进点：共 {len(items)} 条，其中 {len(unassigned)} 条还没挂模式。**\n"
            f"本轮只处理前 {batch} 条，做完报告进度，等下一轮继续。\n"
            "```json\n"
            + _j([_abstract_source_ip(r) for r in unassigned[:batch]], 14000)
            + "\n```"
        )
    except Exception as e:  # noqa: BLE001
        parts.append(f"（改进点列表获取失败：{e}）")

    try:
        checks = client.checks_list()
        parts.append(
            f"已有检测器 {checks.get('count')} 条：\n```json\n"
            + _j(checks.get("checks") or [], 3000)
            + "\n```\n"
            "可用词表（写 `when` 只能用这些名字）：\n```json\n"
            + _j(checks.get("vocabulary") or {}, 4000)
            + "\n```"
        )
    except Exception as e:  # noqa: BLE001
        parts.append(f"（检测器清单获取失败：{e}）")

    try:
        pats = client.patterns_list(limit=int(payload.get("limit") or 60))
        brief = [
            {
                "id": p.get("id"),
                "suspect_type": p.get("suspect_type") or "",
                "members": p.get("member_count"),
                "books": p.get("book_count"),
                "status": p.get("status"),
                "remediation_kind": p.get("remediation_kind") or "",
                "landing_target": p.get("landing_target") or "",
                "abstract": str(p.get("abstract_text") or "")[:100],
            }
            for p in (pats.get("patterns") or [])
        ]
        parts.append(f"现有模式（{len(brief)} 条）：\n```json\n" + _j(brief, 9000) + "\n```")
    except Exception as e:  # noqa: BLE001
        parts.append(f"（patterns 列表获取失败：{e}）")

    parts.append(
        "\n任务使命：把改进点抽象成**换一本书还能用**的模式。详细步骤见 "
        "skills/pattern-abstract.md，这里只说要点。\n"
        "\n每组走五步：**归症状 → 写检测器 → 配正反例 → 自验迭代 → 定档记账**。\n"
        "1. 归症状：给一个 `SUSPECT_TYPE`（全大写下划线）。同名才可能是同一个模式。\n"
        "2. 写检测器：在 `learning-buddy-api/configs/checks/` 建 YAML。"
        "`when` 里**只准写结构和几何特征**，绝对页码、块 ID、具体文字一律禁止 —— "
        "写了这些，换本书就失效，服务端也会直接拒绝加载。\n"
        "2b. 词不够用时**加词，别硬凑**：`when` 里只能用上面「可用词表」里的名字。"
        "缺一个概念就往 `configs/checks/vocabulary.yaml` 加一条（名字 + 表达式 + 中文释义 "
        "+ 正反例），中文豁免词清单放 `lexicons.yaml`。这两个文件与检测器同一挂载目录，"
        "**保存即生效，不重建镜像**。\n"
        "   一条表达式真写不出来（例如跨行遍历表格单元格）才写词插件：先把草稿写到 "
        "`configs/checks/vocab_ext/proposals/`（`fs.request_edit` 即可），"
        "`vocab.check_plugin` 跑正反例，全绿后用 `vocab.request_plugin_edit` 申请放进 "
        "`vocab_ext/` 顶层——那里的 `.py` 会在 buddy-worker 进程里执行，所以是独立一张批准卡，"
        "批 `configs/checks/` 不覆盖它。\n"
        "3. 配正反例：写进 `learning-buddy-api/tests/data/checks/`，正例该报、反例不该报。\n"
        "4. 自验迭代：`checks.dry_run` 在真实页上试跑，`checks.run_examples` 量召回和误报。"
        "**不达标就回第 2 步改 YAML**，改完即时生效，你自己能收敛，不用等人。\n"
        "5. 定档记账：同指纹的修复动作完全一致才配定 `rule`，不一致老实写 `playbook` 手册。"
        "然后 `patterns.bind_detector` 绑上，`ip.set_pattern` 把成员挂过去。\n"
        "\n硬规则：\n"
        "- 这两个目录在写白名单内，可以直接写。**`learning-buddy-api/app/` 不在**，"
        "改了会被整轮回滚；需要动 Python 就走 `fs.request_edit` 请批准。\n"
        "- 判不准就别判，留空比瞎填害处小；\n"
        "- 单书补丁（缺图资源、单页印刷错）明确标 `L3.book_patch`，别硬凑检测器；\n"
        "- 单轮预算 900 秒（等人点批准卡不计入）/ 120 次工具调用 / 12 个文件。"
        "全书自动改按页数加长。**超了整轮回滚**，"
        "所以一轮只做一批，做完报进度；\n"
        "- **不要**用 `harness.run_golden` 当证据（对 MinerU 线是假验证）。\n"
        "\n收尾契约：最终报告 JSON 含 processed_ips、new_symptoms（每条带 suspect_type、"
        "recall、false_positive_rate、remediation_kind）、reused_patterns、"
        "unabstractable（连同原因）、remaining、summary。"
    )
    return "\n\n".join(parts)


register_scenario(
    Scenario("pattern_abstract", "改进点抽象成可复用模式", _pattern_abstract_context)
)


# ---------------- 场景：一书改进点（整理 / 拆分 / 聚合） ----------------


def _ip_book_intent(payload: dict[str, Any]) -> str:
    intent = str(payload.get("intent") or "").strip().lower()
    if intent in ("triage", "split_all", "split_one", "aggregate"):
        return intent
    if str(payload.get("focus") or "").strip().lower() == "aggregate":
        return "aggregate"
    if payload.get("ip_id") and not payload.get("process_all"):
        return "split_one"
    if payload.get("process_all") and str(payload.get("intent") or "") == "split_all":
        return "split_all"
    return "triage"


def _ip_book_context(client: BuddyApiClient, payload: dict[str, Any]) -> str:
    intent = _ip_book_intent(payload)
    parts: list[str] = [
        "### 场景：本书改进点（ip_book · 一书一对话）\n",
        f"**当前意图 intent=`{intent}`**（见 skills/ip-book.md）\n",
        "用户输入：\n```json\n" + _j(payload, 1500) + "\n```",
    ]
    fp = str(payload.get("fingerprint_hash") or "").strip()
    ip_id = str(payload.get("ip_id") or "").strip()
    process_all = bool(payload.get("process_all", True))

    if not fp and intent != "split_one":
        parts.append("**缺少 fingerprint_hash**，请先选定书。")
        return "\n\n".join(parts)

    # 共用预览：整理队列 + 拆分队列 + 对立 hint（按意图裁剪强调）
    if fp and intent in ("triage", "aggregate"):
        try:
            listing = client.ip_list_for_triage(fingerprint_hash=fp, limit=2000)
            total = int(listing.get("total_remaining") or listing.get("count") or 0)
            parts.append(
                f"**待整理 total_remaining={total}**"
                f"（batch={listing.get('count')}, has_more={listing.get('has_more')}）。\n"
                "样本：\n```json\n"
                + _j((listing.get("items") or [])[:40], 8000)
                + "\n```"
            )
        except Exception as e:  # noqa: BLE001
            parts.append(f"（list_for_triage 失败：{e}）")
        try:
            hints = client.ip_conflict_hints(fingerprint_hash=fp)
            parts.append(
                "规则对立 hint（参考，不写库）：\n```json\n"
                + _j(hints.get("hints") or [], 4000)
                + "\n```"
            )
        except Exception as e:  # noqa: BLE001
            parts.append(f"（conflict_hints 失败：{e}）")

    if fp and intent == "split_all":
        try:
            listing = client.ip_list_for_split(fingerprint_hash=fp, limit=2000)
            total = int(listing.get("total_remaining") or listing.get("count") or 0)
            parts.append(
                f"**待拆分 total_remaining={total}**"
                f"（batch={listing.get('count')}, has_more={listing.get('has_more')}）。\n"
                "样本：\n```json\n"
                + _j((listing.get("items") or [])[:30], 8000)
                + "\n```"
            )
        except Exception as e:  # noqa: BLE001
            parts.append(f"（list_for_split 失败：{e}）")

    if intent == "split_one" and ip_id:
        try:
            prop = client.ip_split_propose(ip_id)
            parts.append("当前拆分探测：\n```json\n" + _j(prop, 12000) + "\n```")
        except Exception as e:  # noqa: BLE001
            parts.append(f"（split_propose 失败：{e}）")

    if intent == "aggregate":
        member_ids = [
            str(x).strip()
            for x in (payload.get("member_ids") or [])
            if str(x).strip()
        ]
        gid = str(payload.get("aggregate_group_id") or "").strip()
        process_all = bool(payload.get("process_all")) or not member_ids
        idx = payload.get("aggregate_cluster_index")
        label = str(payload.get("aggregate_cluster_label") or "").strip()
        if not label and idx not in (None, ""):
            label = f"簇#{idx}"
        if not process_all and member_ids:
            parts.append(
                f"\n**单簇范围（硬约束）**：{label or '指定簇'}；"
                f"aggregate_cluster_label=`{label or '（无）'}`；"
                f"aggregate_group_id=`{gid or '（无）'}`；\n"
                f"member_ids={member_ids}\n"
                f"只裁决 {label or '本簇'}。**禁止**处理其它簇；"
                "**不要**询问「全部还是一个」——范围已定为本簇。"
            )
            parts.append(
                f"\n任务使命（处理聚合 · 仅{label or '本簇'}）：\n"
                f"1. 仅针对 {label or '本簇'} 的 member_ids 成对说明 keep/delete；\n"
                "2. **须用户确认后** `ip.aggregate_resolve`（只含本簇 id）；\n"
                "3. 勿静默删除；报告含 aggregate_cluster_label、kept、deleted、summary。"
            )
        else:
            parts.append(
                "\n任务使命（处理全部聚合）：\n"
                "1. `ip.conflict_hints` / 列表找本书全部对立簇，按簇#1、簇#2…编号；\n"
                "2. 逐簇向用户成对说明 keep/delete；\n"
                "3. **须用户确认后**再 `ip.aggregate_resolve`；\n"
                "4. 勿静默删除；报告按簇汇总 kept / deleted / summary。"
            )
    elif intent == "split_all":
        parts.append(
            "\n任务使命（全部拆分）：\n"
            "1. `ip.list_for_split(limit=2000)`；\n"
            "2. 每条 propose→校验→apply；\n"
            "3. 再 list 直到 total_remaining=0；\n"
            "4. 点「全部拆分」= 已授权写库；\n"
            "5. 报告 split_done_count、remaining_needs_split=0。\n"
            "after 分完且不重叠；禁止整包复制父 procedure。"
        )
    elif intent == "split_one":
        if not ip_id:
            parts.append("**缺少 ip_id**（拆分本条）。")
        else:
            parts.append(
                "\n任务使命（拆分本条）：\n"
                "1. 规划子条完备路径；\n"
                "2. `ip.split_propose(ip_id, children)`；\n"
                "3. **展示提案**，确认后 `split_apply`；\n"
                "4. after 分完且不重叠；报告 new_ids / errors / summary。"
            )
    else:
        mission = (
            "\n任务使命（整理）：\n"
            "1. `ip.list_for_triage(limit=2000)`；\n"
            "2. 每条分类 → `ip.triage_apply`（禁止故意漏网）；\n"
            "3. 再 list 直到 total_remaining=0；\n"
            "4. 报告 ready/split/aggregate + **remaining_needs_triage=0**。\n"
            "同条既拆又对立 → 先 split。"
        )
        if process_all:
            mission += "\n用户要求 **process_all**：必须清空该书全部待整理。"
        parts.append(mission)
    return "\n\n".join(parts)


register_scenario(Scenario("ip_book", "本书改进点", _ip_book_context))
# 旧 kind 别名：历史会话未升级时仍能拼上下文
register_scenario(Scenario("ip_triage", "本书改进点", _ip_book_context))
register_scenario(Scenario("ip_split", "本书改进点", _ip_book_context))


def build_task_context(settings: Settings) -> Callable[[str, dict[str, Any]], str]:
    client = BuddyApiClient(settings)

    def builder(kind: str, payload: dict[str, Any]) -> str:
        scenario = SCENARIOS.get(kind)
        if scenario is None:
            raise ValueError(f"未知场景: {kind}")
        return scenario.build_context(client, payload)

    return builder

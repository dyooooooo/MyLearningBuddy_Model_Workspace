"""Improvement Point (IP) lifecycle: ingest attempts → converge → confirm → dual track.

Accepted IPs require a procedure (successful tool path). Only accepted concrete
tracks write corpus_corrections for fingerprint replay.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    CorpusBookEditionBind,
    CorpusCorrection,
    CorpusImprovementLink,
    CorpusImprovementPattern,
    CorpusImprovementPoint,
    CorpusImportJob,
    EditionCorrectionQueue,
)
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

_PATTERN_PROMOTE_PAGES = 5
_REL_TYPES = frozenset(
    {"same_locus", "duplicate", "same_pattern", "supersedes", "conflicts", "depends_on"}
)
# 类目关键词打分表：(类目, 独有判据, 共享词)。
# caption 既可能是图题也可能是表题，"图"/"表格" 也会同时出现在一句话里，所以不能
# 首次命中就返回 —— 那样声明在前的类目会把后面的抢空（表格标题曾全部落进 figure）。
_STRONG_WEIGHT = 2
_WEAK_WEIGHT = 1
_CAT_HINTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("figure", ("插图", "配图", "图片", "图注", "图题", "缺图", "figure"), ("图", "caption")),
    ("math", ("公式", "latex", "分式", "角度", "math"), ()),
    ("exercise", ("选项", "习题", "小问", "mcq"), ("（1）", "(1)")),
    ("table", ("表格", "表宽", "表头", "单元格", "table"), ("caption",)),
    ("layout", ("分栏", "侧栏", "落栏", "阅读序", "layout", "zone"), ()),
    ("header", ("页眉", "页脚", "running_header", "chrome"), ()),
    ("title", ("标题", "例题", "heading", "title"), ()),
)

# 落地目标闭集。每个取值对应一个真实存在且已验证生效的写入面 —— 定路由就是回答
# 「这条规律最终写到哪个文件」。声明式 layout 规则唯一能表达的动作是把块判进
# 某个 zone（见 app/services/ir/layout/schema.py 的 LayoutRule），所以绝大多数
# 图/公式/表格类规律并不属于 L1.layout_rule。
LANDING_TARGETS: dict[str, str] = {
    "L1.layout_rule": "版式规则（zone 判定）→ configs/editions/<id>/layout_rules.yaml",
    "L1.config_pattern": "识别正则与几何阈值 → config.json 的 patterns.* / geometry.*",
    "L1.theme_css": "排版样式（表宽、选项网格、行高）→ assets/theme_css",
    "L0.normalize": "文本归一化（如 CJK 字符混进 LaTeX）→ Python + pytest",
    "L0.renderer": "渲染器行为 → Python 渲染代码",
    "L3.book_patch": "不可规则化（缺图资源、单页配图）→ 保持专书补丁，不落规则",
}

# 曾经这里有一张「类目 → 默认生效面」的关键词先验表，新建模式时直接填上，备注写
# 「按类目先验推定，未经确认」。它猜不准：同属 figure，「装饰图误入主栏」是 zone
# 规则，「缺图未恢复」只能留专书补丁。更糟的是它让「已定路由」的统计看起来很好看，
# 掩盖了真正没人判过的事实。现在一律留空，由 Agent 或人显式定。


# 模式生命周期：draft（新建）→ candidate（够格占用人工注意力）→ routed（已定落地目标）
# → landed（已写入生效面，landed_ref 有值）；no_landing 是终态（判为不可规则化）。
# "approved" 是历史取值，只为兼容旧数据保留。
# needs_ip_split 是死胡同：一条改进点在确认时就记了好几件事，模式只有一个成员，
# patterns.split 搬不动（不允许把成员搬空）。得回确认轨把改进点拆成单主题再抽象。
PATTERN_STATUSES = (
    "draft",
    "candidate",
    "routed",
    "landed",
    "no_landing",
    "needs_ip_split",
    "approved",
)
# 新成员可以并进来的状态；no_landing 与 needs_ip_split 已关闭，不再吸收成员。
PATTERN_MATCHABLE_STATUSES = ("draft", "candidate", "routed", "landed", "approved")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def infer_category(summary: str = "", kind: str = "", op: str = "") -> str:
    """Pick the highest-scoring category rather than the first keyword hit.

    Ties keep :data:`_CAT_HINTS` order, so behaviour stays deterministic.
    """
    blob = f"{summary} {kind} {op}".lower()
    best, best_score = "other", 0
    for cat, strong, weak in _CAT_HINTS:
        score = _STRONG_WEIGHT * sum(1 for k in strong if k.lower() in blob)
        score += _WEAK_WEIGHT * sum(1 for k in weak if k.lower() in blob)
        if score > best_score:
            best, best_score = cat, score
    return best


def make_locus_key(
    *,
    page_no: int,
    category: str,
    block_ids: list[str] | None = None,
    slot: str = "",
) -> str:
    bids = sorted({str(b).strip() for b in (block_ids or []) if str(b).strip()})
    slot_s = (slot or "").strip() or ("blocks:" + ",".join(bids[:6]) if bids else "page")
    return f"page:{int(page_no)}:cat:{category or 'other'}:slot:{slot_s[:120]}"


# 改进点来源闭集（谁发起）：动手改页的都是 AI，只分自主分析 vs 用户要求。
IP_ORIGINS: dict[str, str] = {
    "ai_auto": "AI 自主改",
    "user_ask": "用户让 AI 改",
}

# 历史 claim.origin → 新闭集；空串表示不应进改进点表（探针/流水线事件）。
_LEGACY_ORIGIN_MAP: dict[str, str] = {
    "ai_auto": "ai_auto",
    "user_ask": "user_ask",
    "fullbook_ai": "ai_auto",
    "agent_page": "user_ask",
    "human_confirm": "user_ask",
    "migrated_l3": "user_ask",
    "pipeline_event": "",
    "test_probe": "",
}

# 会话 → 旧来源码（再经 normalize_origin 映射到闭集）。
_SESSION_ORIGIN = {
    "cursor-ide-migration": "migrated_l3",
    "accept-ui": "human_confirm",
}

# 兼容旧 UI / 报告：仍暴露旧标签时用；新列表只用 IP_ORIGINS。
_ORIGIN_LABELS = {
    **IP_ORIGINS,
    "fullbook_ai": "全文 AI",
    "human_confirm": "人工确认",
    "agent_page": "Agent 改页",
    "pipeline_event": "流水线事件",
    "migrated_l3": "历史补丁迁入",
    "test_probe": "测试探针",
}


_DUP_FILLER_RE = re.compile(r"[\s，。；、：,.;:!！?？（）()\[\]「」『』【】/·—\-～~“”\"']+")


def _dup_key(summary: str) -> str:
    """同文判定键：去掉空白与标点，只比实质文字。"""
    return _DUP_FILLER_RE.sub("", str(summary or ""))


def _is_test_probe(claim: dict[str, Any], concrete: dict[str, Any]) -> bool:
    if "【测试探针】" in str(claim.get("user_input") or ""):
        return True
    for src in (claim.get("causal_chain"), concrete.get("causal_chain")):
        if isinstance(src, dict) and src.get("user_input_source") == "manual_residual_fix":
            return True
    return False

_PAGE_EDIT_TOOLS = {
    "patch_block",
    "replace_page_blocks",
    "corpus.patch_block",
    "corpus.replace_page_blocks",
    "cursor_ir",
    "layout_rebuild",
    "content_correct",
    "rerender",
}


def normalize_origin(raw: str) -> str:
    """Map any origin string to the closed set ``ai_auto`` / ``user_ask``.

    Returns empty string for origins that must not enter the IP table
    (test probes, pipeline events).
    """
    key = (raw or "").strip().lower()
    if key in IP_ORIGINS:
        return key
    if key in _LEGACY_ORIGIN_MAP:
        return _LEGACY_ORIGIN_MAP[key]
    return "user_ask" if key else ""


def guess_needs_split(summary: str) -> bool:
    """Heuristic: multi-topic or cross-page batch summaries need a human split."""
    sm = summary or ""
    if re.search(r"第\s*\d+\s*批", sm) or "必修二第" in sm:
        return True
    if re.search(r"覆盖\s*p\d+", sm, re.I) or re.search(r"p\d+/\d+", sm, re.I):
        return True
    seps = sm.count("；") + sm.count("; ")
    if seps >= 2:
        return True
    if seps >= 1 and len(sm) > 80:
        return True
    if seps >= 1 and any(x in sm for x in ("并且", "同时", "以及", "还要", "另外")):
        return True
    return False


# 预抽象整理状态（与 lifecycle status=accepted 无关）
# 闭集：待整理 →（Cursor Agent 整理）→ 待拆分 | 需聚合 | 完成体
PREP_NEEDS_TRIAGE = "needs_triage"
PREP_NEEDS_SPLIT = "needs_split"
PREP_NEEDS_AGGREGATE = "needs_aggregate"
PREP_NEEDS_CONFLICT = "needs_conflict_resolve"  # 旧码，读时归一为 needs_aggregate
PREP_READY = "ready"
PREP_STATUS_LABELS: dict[str, str] = {
    PREP_NEEDS_TRIAGE: "待整理",
    PREP_NEEDS_SPLIT: "待拆分",
    PREP_NEEDS_AGGREGATE: "需聚合",
    PREP_NEEDS_CONFLICT: "需聚合",
    PREP_READY: "完成体",
}
_PREP_CANONICAL = frozenset(
    {PREP_NEEDS_TRIAGE, PREP_NEEDS_SPLIT, PREP_NEEDS_AGGREGATE, PREP_READY}
)
# 可参与矛盾 hint 扫描的整理状态（仅 hint，不写终态）
_PREP_CONFLICT_SCAN = frozenset(
    {PREP_READY, PREP_NEEDS_AGGREGATE, PREP_NEEDS_CONFLICT, PREP_NEEDS_TRIAGE}
)
_OPPOSING_VERB_PAIRS: tuple[tuple[str, str], ...] = (
    ("删除", "保留"),
    ("删掉", "留下"),
    ("去掉", "加上"),
    ("移除", "添加"),
    ("取消", "恢复"),
    ("隐藏", "显示"),
    ("勿", "要"),
    ("不要", "需要"),
)


class PrepGateError(ValueError):
    """prep_status 非 ready，禁止挂抽象轨。"""


def normalize_prep_status(code: str) -> str:
    key = (code or "").strip()
    if key == PREP_NEEDS_CONFLICT:
        return PREP_NEEDS_AGGREGATE
    if key in _PREP_CANONICAL:
        return key
    return PREP_NEEDS_TRIAGE


def prep_status_label(code: str) -> str:
    key = normalize_prep_status(code)
    return PREP_STATUS_LABELS.get(key, "待整理")


def _row_prep(row: CorpusImprovementPoint) -> str:
    key = str(getattr(row, "prep_status", None) or "").strip()
    if key:
        return normalize_prep_status(key)
    if int(getattr(row, "needs_split", 0) or 0):
        return PREP_NEEDS_SPLIT
    return PREP_NEEDS_TRIAGE


def _set_prep(
    row: CorpusImprovementPoint,
    prep: str,
    *,
    now: str | None = None,
) -> None:
    """双写 prep_status 与 needs_split。"""
    key = normalize_prep_status(prep)
    row.prep_status = key
    row.needs_split = 1 if key == PREP_NEEDS_SPLIT else 0
    if now:
        row.updated_at = now


def _summaries_look_similar(a: str, b: str) -> bool:
    ka, kb = _dup_key(a), _dup_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # 短摘要：共享前缀；长摘要：字符重叠比
    shorter, longer = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    if len(shorter) >= 4 and shorter in longer:
        return True
    if len(shorter) < 6:
        return False
    overlap = sum(1 for ch in set(shorter) if ch in longer)
    return overlap / max(len(set(shorter)), 1) >= 0.55


def _opposing_verbs(a: str, b: str) -> str:
    """若摘要动词对立，返回原因短句；否则空串。"""
    for left, right in _OPPOSING_VERB_PAIRS:
        a_has_l, a_has_r = left in a, right in a
        b_has_l, b_has_r = left in b, right in b
        if (a_has_l and b_has_r and not a_has_r) or (a_has_r and b_has_l and not a_has_l):
            return f"对立动词：{left} / {right}"
    return ""


def _link_conflicts(
    session: Session, a_id: str, b_id: str
) -> None:
    existing = (
        session.query(CorpusImprovementLink)
        .filter(
            CorpusImprovementLink.rel == "conflicts",
            (
                (CorpusImprovementLink.from_id == a_id)
                & (CorpusImprovementLink.to_id == b_id)
            )
            | (
                (CorpusImprovementLink.from_id == b_id)
                & (CorpusImprovementLink.to_id == a_id)
            ),
        )
        .first()
    )
    if existing is None:
        _add_link(session, a_id, b_id, "conflicts")


def detect_and_mark_conflicts(
    session: Session,
    *,
    fingerprint_hash: str,
    focus_id: str = "",
) -> list[dict[str, Any]]:
    """同书轻量矛盾检测：命中则双方标 needs_conflict_resolve。"""
    fp = (fingerprint_hash or "").strip()
    if not fp:
        return []
    rows = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.fingerprint_hash == fp)
        .filter(CorpusImprovementPoint.status.in_(("accepted", "effective", "conflict")))
        .all()
    )
    open_rows = [r for r in rows if _row_prep(r) in _PREP_CONFLICT_SCAN]
    if focus_id:
        focus = next((r for r in rows if r.id == focus_id), None)
        if focus is not None and focus not in open_rows and _row_prep(focus) != PREP_NEEDS_SPLIT:
            open_rows.append(focus)
    pairs: list[dict[str, Any]] = []
    now = _now_iso()
    for i, left in enumerate(open_rows):
        for right in open_rows[i + 1 :]:
            if int(left.page_no or 0) != int(right.page_no or 0):
                continue
            sa = str(getattr(left, "summary", None) or "")
            sb = str(getattr(right, "summary", None) or "")
            reason = _opposing_verbs(sa, sb)
            similar = _summaries_look_similar(sa, sb)
            linked = (
                session.query(CorpusImprovementLink)
                .filter(
                    CorpusImprovementLink.rel == "conflicts",
                    (
                        (CorpusImprovementLink.from_id == left.id)
                        & (CorpusImprovementLink.to_id == right.id)
                    )
                    | (
                        (CorpusImprovementLink.from_id == right.id)
                        & (CorpusImprovementLink.to_id == left.id)
                    ),
                )
                .first()
                is not None
            )
            if not ((reason and similar) or linked):
                continue
            if not reason:
                reason = "已有 conflicts 链接" if linked else "摘要对立"
            _set_prep(left, PREP_NEEDS_AGGREGATE, now=now)
            _set_prep(right, PREP_NEEDS_AGGREGATE, now=now)
            _link_conflicts(session, left.id, right.id)
            pairs.append(
                {
                    "a_id": left.id,
                    "b_id": right.id,
                    "page_no": int(left.page_no or 0),
                    "reason": reason,
                    "summary_a": sa[:200],
                    "summary_b": sb[:200],
                }
            )
    if pairs:
        session.flush()
    return pairs


def _infer_origin(
    *,
    claim: dict[str, Any],
    concrete: dict[str, Any],
    evidence: dict[str, Any],
    procedure: dict[str, Any],
    summary: str,
    authority: str = "",
    session_id: str = "",
    stored_origin: str = "",
) -> tuple[str, str]:
    """Return (origin_code, origin_label) in the closed set ai_auto | user_ask."""
    # Prefer persisted column when already normalized.
    col = normalize_origin(stored_origin)
    if col in IP_ORIGINS:
        return col, IP_ORIGINS[col]

    explicit = str(claim.get("origin") or concrete.get("origin") or "").strip()
    mapped = normalize_origin(explicit)
    if mapped in IP_ORIGINS:
        return mapped, IP_ORIGINS[mapped]

    # 真实来源优先：会话身份 / 测试探针，都比摘要关键词可靠
    if _is_test_probe(claim, concrete):
        return "user_ask", IP_ORIGINS["user_ask"]  # probes should be deleted; fallback label
    sess_origin = _SESSION_ORIGIN.get(str(session_id or "").strip())
    if sess_origin:
        mapped = normalize_origin(sess_origin)
        if mapped in IP_ORIGINS:
            return mapped, IP_ORIGINS[mapped]

    text = (summary or str(claim.get("summary") or "")).strip()
    kind = str(claim.get("kind") or "").strip().lower()
    auth = (authority or "").strip().lower()
    src = str(concrete.get("source") or "").strip().lower()

    if auth == "pipeline" or "越权" in text or "pipeline" in text.lower():
        return "user_ask", IP_ORIGINS["user_ask"]
    if re.search(r"第\s*\d+\s*批", text) or "必修二第" in text:
        return "ai_auto", IP_ORIGINS["ai_auto"]
    if src in ("ai_import", "ai_batch", "fullbook_ai"):
        return "ai_auto", IP_ORIGINS["ai_auto"]
    if kind == "migrated_correction" or "历史 correction" in text or "历史补丁" in text:
        return "user_ask", IP_ORIGINS["user_ask"]
    if src in ("ai_page_edit",) or kind in ("cursor_ir", "human_confirm"):
        return "user_ask", IP_ORIGINS["user_ask"]

    attempts = evidence.get("attempts") if isinstance(evidence, dict) else []
    if not isinstance(attempts, list):
        attempts = []
    proposers = {
        str(a.get("proposer") or "").lower()
        for a in attempts
        if isinstance(a, dict)
    }
    if "user" in proposers:
        return "user_ask", IP_ORIGINS["user_ask"]

    tools: set[str] = set()
    for a in attempts:
        if not isinstance(a, dict):
            continue
        for key in ("tool", "op", "mode"):
            t = str(a.get(key) or "").strip().lower()
            if t:
                tools.add(t)
        aid = str(a.get("id") or "")
        if aid.startswith("mig.ec.") or aid.startswith("ui."):
            # mig.ec = 会话 effective_changes 补录；ui. = Studio 确认卡
            return "user_ask", IP_ORIGINS["user_ask"]

    steps = procedure.get("steps") if isinstance(procedure, dict) else []
    if isinstance(steps, list):
        for s in steps:
            if isinstance(s, dict) and s.get("tool"):
                tools.add(str(s.get("tool") or "").strip().lower())

    # 默认：对话改页 = 用户让 AI 改
    return "user_ask", IP_ORIGINS["user_ask"]


def _apply_layers_and_plan(
    *,
    status: str,
    authority: str,
    pattern_status: str,
) -> tuple[list[str], str, str]:
    """Return (layers, primary_layer_label, future_plan)."""
    st = (status or "").strip().lower()
    auth = (authority or "edition").strip().lower()
    pat = (pattern_status or "none").strip().lower()

    if st in ("observed", "clustered", "candidate"):
        return (
            ["L4"],
            "L4 过程账",
            "尚未确认：只留在本任务过程账，不回放、不影响下一本。确认后才会进专书补丁。",
        )
    if st == "rejected":
        return (
            ["L4"],
            "已否决",
            "已否决：不进回放，不晋升方案/代码，仅作台账留存。",
        )
    if st == "superseded":
        return (
            ["L4"],
            "旧版痕迹",
            "旧版：已被更好结果替换；不参与默认回放。查阅用，不自动影响下一本。",
        )
    if st == "conflict":
        return (
            ["L4"],
            "待裁决",
            "冲突未决：需先留下一条。定稿后按有效改进进入 L3（及可选 L1/L0）。",
        )
    if st != "accepted":
        return (["L4"], "过程", "状态未进入有效集，暂不规划跨书应用。")

    # accepted
    if auth == "pipeline":
        return (
            ["L0", "L2"],
            "L0 流水线",
            "越权类：目标是改全库流水线代码（L0）。本书可绑定方案（L2），"
            "但不会把页补丁抄到下一本；修好代码后后续书默认受益。",
        )
    layers = ["L3", "L2"]
    if pat in ("draft", "approved"):
        layers = ["L3", "L2", "L1"]
        return (
            layers,
            "L3→L1",
            "专书页补丁（L3）+ 已绑方案（L2）；已标可复用草案，"
            "晋升落地后进教材族规则（L1），同类下一本吃规则而非抄页。",
        )
    if pat in ("landed", "verified"):
        layers = ["L1", "L3", "L2"]
        return (
            layers,
            "L1 已落地",
            "规则已进教材族（L1）：下一本/下下一本默认受益；"
            "本书仍保留 L3 页补丁以便重导恢复。",
        )
    # pattern none
    return (
        ["L3", "L2"],
        "L3 仅专书",
        "仅本书：同书重导按页回放（L3）并走已绑方案（L2）。"
        "不自动影响下一本；若要推广，请标成「可复用草案」。",
    )


_MODE_LABELS = {
    "fs_violation": "文件守卫拦截（越权后回滚）",
    "fs_edit": "已批准的核心/流水线文件编辑",
    "content_correct": "内容或流水线修正",
    "layout_rebuild": "版式重建",
    "cursor_ir": "页内块编辑",
}


def _as_str_list(val: Any) -> list[str]:
    if not isinstance(val, list):
        return []
    out: list[str] = []
    for x in val:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def _pick_str(*vals: Any, limit: int = 2000) -> str:
    """Pick first non-empty string. limit<=0 means keep full text (no truncation)."""
    for v in vals:
        s = str(v or "").strip()
        if s:
            return s if limit <= 0 else s[:limit]
    return ""


_PAGE_NO_RE = re.compile(r"page_no\s*=\s*(\d+)", re.I)


def _page_from_text(text: str) -> int:
    m = _PAGE_NO_RE.search(text or "")
    return int(m.group(1)) if m else 0


def _strip_focus_boilerplate(text: str) -> str:
    """Keep the user's real ask; drop Studio「聚焦目标」模板行。"""
    t = (text or "").strip()
    if not t:
        return ""
    if "【聚焦目标】" not in t and "page_no=" not in t:
        return t
    # Only skip template/instruction lines, not the user's free text after them
    body: list[str] = []
    for ln in t.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith(("line=", "job_id=", "page_no=", "block_id=", "【聚焦目标】")):
            continue
        if s.startswith("请基于该页") or s.startswith("改动生效后"):
            continue
        if "记入当前解析方案" in s or "你无需代用户决定" in s:
            continue
        body.append(s)
    # Empty body = user only sent the focus template; do not fall back to truncating it.
    return "\n".join(body).strip()


def _is_control_ask(text: str) -> bool:
    """确认/操控口令，不是问题根源（打开方案、按建议改、记入…）。"""
    t = (text or "").strip()
    if not t:
        return True
    if t.startswith("打开方案") or t.startswith("按建议改"):
        return True
    if t in ("继续", "不修", "下一批", "好的", "ok", "OK", "是", "否"):
        return True
    if "记入当前" in t and len(t) < 80:
        return True
    if "待审队列" in t and len(t) < 80:
        return True
    if t.startswith("Edition ") and len(t) < 40:
        return True
    return False


def normalize_direct_summary(summary: str) -> str:
    """直奔主题：可执行改动说明（删/留/勿/改为），去掉页前缀、操控口令与内部黑话。"""
    s = " ".join((summary or "").replace("\n", " ").split()).strip()
    if not s:
        return ""
    # 列表已有页列 → 摘要里不必再写「第 N 页」前缀
    s = re.sub(r"^第\s*\d+\s*页\s*[：:]\s*", "", s)
    s = re.sub(r"^第\s*\d+\s*页(?=[表图例练侧左主旁])", "", s)
    # 操控口令污染（旧 headline 拼出来的）
    s = re.sub(r"^(?:记入当前|打开方案|按建议改)[^·]*·\s*", "", s)
    jargon = (
        (r"的\s*skip_render", "的隐藏"),
        (r"\bskip_render\b", "隐藏"),
        (r"[（(]\s*figure_size\s*=\s*half\s*[）)]", "（图宽约半栏）"),
        (r"[（(]\s*lb-table--wide\s*[）)]", "（加宽表）"),
        (r"\blb-table--wide\b", "加宽表"),
        (r"[（(]\s*running_header\s*[）)]", "（页眉）"),
        (r"\brunning_header\b", "页眉"),
        (r"废止segments", "废止分段缓存"),
        (
            r"（历史 correction，无 note；保留 after_json 回放）",
            "（历史迁移页补丁，当时无改动说明）",
        ),
        (r"保留 after_json 回放", "保留页补丁回放"),
    )
    for pat, repl in jargon:
        s = re.sub(pat, repl, s, flags=re.IGNORECASE)
    # 误伤 / 历史改写残留修复
    s = s.replace("meta.图宽约半栏", "meta.figure_size=half")
    s = s.replace(
        "前言页结构整理（历史迁移页补丁（当时无改动说明））",
        "前言页：结构整理（历史迁移页补丁，当时无改动说明）",
    )
    s = s.replace(
        "前言页结构整理（历史迁移页补丁（当时无改动说明）",
        "前言页：结构整理（历史迁移页补丁，当时无改动说明）",
    )
    s = s.replace("取消表6-1标题的 隐藏渲染", "取消表6-1标题隐藏")
    s = s.replace("取消表6-1标题的 隐藏", "取消表6-1标题隐藏")
    return s[:2000]


def _user_ask_for_summary(
    user_input: str, user_requests: list[str] | None = None
) -> str:
    """Pick the most characteristic user utterance (never control/confirm commands)."""
    ordered: list[str] = []
    if user_input:
        ordered.append(user_input)
    for u in reversed(list(user_requests or [])):
        if u and u not in ordered:
            ordered.append(u)
    # Prefer short follow-ups (no focus header) — they are the unique ask
    for t in ordered:
        if "【聚焦目标】" not in t and "page_no=" not in t:
            ask = t.strip()
            if ask and not _is_control_ask(ask):
                return ask
    for t in ordered:
        ask = _strip_focus_boilerplate(t)
        if ask and not _is_control_ask(ask):
            return ask
    return ""


def _headline_from_user_ask(
    *,
    user_ask: str,
    summary: str,
    mode: str,
) -> str:
    """List headline = 直奔主题摘要；用户原话留在 user_input 字段，不抢摘要位。"""
    sum_one = normalize_direct_summary(summary)
    if "越权" in (summary or "") and ("回滚" in (summary or "") or mode == "fs_violation"):
        return sum_one or "Agent 改动了白名单外文件，系统已拦截并回滚"
    if mode == "fs_edit" or "Pipeline" in (summary or "") or "流水线" in (summary or ""):
        return sum_one or "已批准的流水线/核心文件改动"
    if sum_one:
        return sum_one
    one = " ".join((user_ask or "").replace("\n", " ").split()).strip()
    if one and not _is_control_ask(one):
        return one[:200] + ("…" if len(one) > 200 else "")
    return ""


def _resolve_page_no(
    *,
    page_no: int = 0,
    concrete: dict[str, Any] | None = None,
    block_ids: list[str] | None = None,
) -> int:
    """Prefer explicit page; else dialogue focus / causal_chain / concrete."""
    if int(page_no or 0) > 0:
        return int(page_no)
    c = concrete if isinstance(concrete, dict) else {}
    for key in ("page_no", "page"):
        try:
            n = int(c.get(key) or 0)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    cc = c.get("causal_chain") if isinstance(c.get("causal_chain"), dict) else {}
    try:
        n = int(cc.get("page_no") or 0)
        if n > 0:
            return n
    except (TypeError, ValueError):
        pass
    for blob in (
        c.get("user_input"),
        c.get("user_prompt"),
        cc.get("user_input_display"),
        *((cc.get("user_requests") or []) if isinstance(cc.get("user_requests"), list) else []),
    ):
        n = _page_from_text(str(blob or ""))
        if n > 0:
            return n
    _ = block_ids  # reserved for future block→page lookup
    return 0


def _display_fields(
    *,
    row: CorpusImprovementPoint,
    claim: dict[str, Any],
    concrete: dict[str, Any],
    evidence: dict[str, Any],
    procedure: dict[str, Any],
    summary: str,
    authority: str,
) -> dict[str, Any]:
    """UI-facing narrative: trigger / user input / what happened / tool steps."""
    paths = _as_str_list(claim.get("paths")) or _as_str_list(concrete.get("paths"))
    if not paths:
        for a in evidence.get("attempts") or []:
            if isinstance(a, dict):
                paths = _as_str_list(a.get("paths"))
                if paths:
                    break
    detail = _pick_str(claim.get("detail"), concrete.get("detail"), limit=2000)
    mode = _pick_str(claim.get("mode"), concrete.get("mode"), limit=80)
    proposer = _pick_str(
        claim.get("proposer"),
        concrete.get("proposer"),
        limit=80,
    )
    # User dialogue: FULL raw messages (focus shell + free text). Never strip for storage/API.
    causal_raw = claim.get("causal_chain") or concrete.get("causal_chain") or {}
    if not isinstance(causal_raw, dict):
        causal_raw = {}
    raw_reqs = [
        str(x)
        for x in (
            causal_raw.get("user_requests_raw")
            or causal_raw.get("user_requests")
            or []
        )
        if str(x).strip()
    ]
    user_input = _pick_str(
        causal_raw.get("user_input_raw"),
        claim.get("user_input_raw"),
        concrete.get("user_input_raw"),
        claim.get("user_input"),
        claim.get("user_prompt"),
        concrete.get("user_input"),
        concrete.get("user_prompt"),
        limit=0,
    )
    causal_chain: dict[str, Any] = {
        # Prefer raw (含【聚焦目标】模板)；兼容旧的已剥壳列表
        "user_requests": raw_reqs
        or [
            str(x)
            for x in (causal_raw.get("user_requests") or [])
            if str(x).strip()
        ],
        "user_requests_raw": raw_reqs,
        "user_input_raw": str(
            causal_raw.get("user_input_raw") or user_input or ""
        ),
        "ai_summary": str(causal_raw.get("ai_summary") or "")[:1500],
        "ai_tools": [
            {
                "tool": str(t.get("tool") or "?"),
                "args_summary": str(t.get("args_summary") or "")[:300],
            }
            for t in (causal_raw.get("ai_tools") or [])
            if isinstance(t, dict)
        ][:12],
        "narrative": str(causal_raw.get("narrative") or "")[:2500],
        "user_input_source": str(causal_raw.get("user_input_source") or ""),
        "historical_fix_summary": str(
            causal_raw.get("historical_fix_summary") or ""
        ),
        "dialogue_seq": causal_raw.get("dialogue_seq"),
        "block_id": str(causal_raw.get("block_id") or ""),
    }

    # Drop cross-page / control mismatches (bad historical backfill)
    ui_page = _page_from_text(user_input)
    row_page = int(row.page_no or 0)
    if ui_page > 0 and row_page > 0 and ui_page != row_page:
        user_input = ""
    free = _strip_focus_boilerplate(user_input) if user_input else ""
    if user_input and _is_control_ask(free or user_input):
        user_input = ""
    if not user_input:
        for u in causal_chain.get("user_requests") or []:
            up = _page_from_text(str(u))
            if row_page > 0 and up > 0 and up != row_page:
                continue
            free_u = _strip_focus_boilerplate(str(u))
            # Keep FULL raw message (template + body), not stripped free text only
            if free_u and not _is_control_ask(free_u):
                user_input = str(u)
                break
            if (
                str(u).strip()
                and "【聚焦目标】" in str(u)
                and not _is_control_ask(free_u or "")
            ):
                user_input = str(u)
                break
    # migrated_l3: do NOT promote summary to user_input (it is AI/system note)
    # Page from dialogue focus when row was wrongly stored as p0
    dlg_page = 0
    try:
        dlg_page = int(causal_raw.get("page_no") or 0)
    except (TypeError, ValueError):
        dlg_page = 0
    if dlg_page <= 0:
        dlg_page = _page_from_text(user_input)
    if dlg_page <= 0:
        for u in causal_chain.get("user_requests") or []:
            dlg_page = _page_from_text(str(u))
            if dlg_page > 0:
                break
    effective_page = int(row.page_no or 0) or dlg_page
    if dlg_page > 0:
        causal_chain["page_no"] = dlg_page
    auth = (authority or str(row.authority or "")).strip().lower()
    auth_label = "越权（需改流水线/核心代码）" if auth == "pipeline" else "非越权（方案/页补丁可吸收）"

    # Headline: 直奔主题摘要（与 summary 列一致）；用户原话另字段展示
    user_ask = _user_ask_for_summary(
        user_input, list(causal_chain.get("user_requests") or [])
    )
    headline = _headline_from_user_ask(
        user_ask=user_ask, summary=summary, mode=mode
    )
    if "越权" in summary and ("回滚" in summary or mode == "fs_violation"):
        if not headline:
            headline = "Agent 改动了白名单外文件，系统已拦截并回滚"
        what_happened = (
            "前因：用户提出需求 → Copilot/Agent 改文件 → "
            "fs_guard（文件改动守护）检出白名单外路径并回滚。"
            "本条记为流水线级（L0）改进点，便于以后改核心代码系统性修复。"
        )
    elif "Pipeline" in summary or "流水线" in summary or mode == "fs_edit":
        if not headline:
            headline = "已批准的流水线/核心文件改动"
        what_happened = (
            "本轮改动落在已批准的核心/主题文件上（仍属 pipeline 权限类），"
            "需后续沉淀进流水线或主题资源，而不是抄页补丁到下一本。"
        )
    else:
        if not headline:
            headline = normalize_direct_summary(summary) or "(无摘要)"
        what_happened = detail or summary or ""

    if detail and detail not in what_happened:
        # keep guard report for pipeline
        pass

    # Procedure steps for UI (dedupe + synthesize from paths when thin)
    raw_steps = procedure.get("steps") if isinstance(procedure, dict) else []
    ui_steps: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(raw_steps, list):
        for s in raw_steps:
            if not isinstance(s, dict):
                continue
            tool = str(s.get("tool") or "?").strip()
            args = str(s.get("args_summary") or s.get("summary") or "").strip()
            key = f"{tool}|{args}"
            if key in seen:
                continue
            seen.add(key)
            ui_steps.append(
                {
                    "tool": tool,
                    "args_summary": args[:300],
                    "at": str(s.get("at") or ""),
                }
            )
    if paths and (
        not ui_steps
        or all(
            ("越权" in (s.get("args_summary") or "") or "Pipeline" in (s.get("args_summary") or ""))
            for s in ui_steps
        )
    ):
        action = "回滚/拦截涉及文件" if mode == "fs_violation" or "回滚" in summary else "改动涉及文件"
        ui_steps = [
            {
                "tool": mode or "fs_guard",
                "args_summary": f"{action}：{', '.join(paths[:12])}"
                + ("…" if len(paths) > 12 else ""),
                "at": "",
            }
        ]

    trigger = {
        "job_id": str(row.source_job_id or ""),
        "session_id": str(row.source_session_id or ""),
        "page_no": int(effective_page or 0),
        "mode": mode,
        "mode_label": _MODE_LABELS.get(mode, mode or "未标注"),
        "proposer": proposer or ("system" if auth == "pipeline" else ""),
        "authority": auth or "edition",
        "authority_label": auth_label,
    }

    lifecycle = _lifecycle_story(
        row=row,
        summary=summary,
        headline=headline,
        user_input=user_input,
        causal_chain=causal_chain,
        what_happened=detail or what_happened,
        paths=paths,
        mode=mode,
        ui_steps=ui_steps,
        procedure=procedure,
        evidence=evidence,
        concrete=concrete,
        authority=auth,
        trigger=trigger,
        effective_page=int(effective_page or 0),
        origin=str(
            concrete.get("origin")
            or claim.get("origin")
            or ""
        ),
    )

    return {
        "headline": headline[:200],
        "what_happened": (detail or what_happened)[:2000],
        "paths": paths[:40],
        "mode": mode,
        "mode_label": trigger["mode_label"],
        "proposer": trigger["proposer"],
        "user_input": user_input,
        "causal_chain": causal_chain,
        "trigger": trigger,
        "procedure_steps_ui": ui_steps[:30],
        "lifecycle": lifecycle,
        "effective_page_no": int(effective_page or 0),
    }


_STATUS_STORY = {
    "observed": ("未确认", "过程观察中，尚未定稿"),
    "clustered": ("未确认", "已归入位点簇，等待确认"),
    "candidate": ("未确认", "候选改进，等待确认"),
    "accepted": ("有效", "已确认：可回放/可晋升"),
    "conflict": ("冲突", "同位点多条有效候选，需裁决"),
    "superseded": ("旧版", "已被更新结果替换"),
    "rejected": ("否决", "明确否决，不进入回放"),
}

_PATTERN_STORY = {
    "none": "仅本书实例（未标可复用）",
    "draft": "可复用草案（待晋升教材族规则）",
    "approved": "已批准待落地",
    "landed": "已落地为教材族规则（L1）",
    "verified": "规则已验证",
}


def _lifecycle_story(
    *,
    row: CorpusImprovementPoint,
    summary: str,
    headline: str,
    user_input: str,
    causal_chain: dict[str, Any],
    what_happened: str,
    paths: list[str],
    mode: str,
    ui_steps: list[dict[str, str]],
    procedure: dict[str, Any],
    evidence: dict[str, Any],
    concrete: dict[str, Any],
    authority: str,
    trigger: dict[str, Any],
    effective_page: int = 0,
    origin: str = "",
) -> dict[str, Any]:
    """Ordered story: user → AI → tools → outcome → lifecycle next."""
    st = str(row.status or "").strip().lower()
    status_label, status_meaning = _STATUS_STORY.get(st, (st or "未知", ""))
    pat = str(row.pattern_status or "none").strip().lower() or "none"
    pattern_label = _PATTERN_STORY.get(pat, pat)
    layers, layer_label, future_plan = _apply_layers_and_plan(
        status=st, authority=authority, pattern_status=pat
    )
    page_show = int(effective_page or row.page_no or 0)
    origin_s = (origin or str(concrete.get("origin") or "")).strip()

    # —— stage 1: user ——
    # 完整保留 Copilot role=user 原文：含 Studio「聚焦目标」固定壳 + 用户正文 + 同轮前后话术。
    # 不剥壳，便于区分界面模板 vs 客户手打内容。
    user_reqs = [
        str(x)
        for x in (
            causal_chain.get("user_requests_raw")
            or causal_chain.get("user_requests")
            or []
        )
        if str(x).strip()
    ]
    hist_sum = str(causal_chain.get("historical_fix_summary") or "").strip()
    primary_raw = str(
        causal_chain.get("user_input_raw") or user_input or ""
    ).strip()
    if primary_raw and _is_control_ask(
        _strip_focus_boilerplate(primary_raw) or primary_raw
    ):
        primary_raw = ""
    if not primary_raw:
        for u in user_reqs:
            free_u = _strip_focus_boilerplate(str(u))
            if free_u and not _is_control_ask(free_u):
                primary_raw = str(u)
                break
            if "【聚焦目标】" in str(u):
                primary_raw = str(u)
                break
    if primary_raw:
        stage_user_body = primary_raw
    elif origin_s == "fullbook_ai":
        stage_user_body = (
            "全文导入 AI 首轮批量改动：无单次用户口令。"
            "这是导入阶段模型自动产生的改进；其后的页级操作应都有用户对话。"
        )
    elif origin_s == "migrated_l3" or hist_sum:
        stage_user_body = (
            "无 Copilot 用户口令（历史迁入）。\n"
            "下面是当时的改法摘要（AI/系统记述，不是用户打的字）：\n"
            + (hist_sum or summary or "（无）")
        )
    elif str(row.source_session_id or "").startswith("cursor-ide"):
        stage_user_body = (
            "Cursor 迁移场写入：没有 Studio 用户口令。\n"
            "改法摘要（非用户输入）：" + (summary or "（无）")
        )
    else:
        stage_user_body = (
            "未找到该页对应的用户原文。"
            "正常路径应含【聚焦目标】固定格式 + 用户正文；"
            "不要把 AI 改法摘要当成用户输入。"
        )
    # 同轮全部用户消息（原文）：聚焦前 / 主聚焦 / 后续确认
    arc_items: list[dict[str, str]] = []
    for i, u in enumerate(user_reqs):
        raw = str(u).strip()
        if not raw:
            continue
        if raw == primary_raw:
            continue
        free_u = _strip_focus_boilerplate(raw)
        if "【聚焦目标】" in raw:
            label = "聚焦消息"
        elif _is_control_ask(free_u or raw):
            label = "确认/操控"
        elif i == 0 and "【聚焦目标】" not in raw:
            label = "聚焦前"
        else:
            label = "同轮用户消息"
        arc_items.append({"text": f"[{label}]\n{raw}"})
    stage_user = {
        "key": "user_input",
        "title": "1. 用户输入（完整原文：聚焦格式 + 正文 + 同轮对话）",
        "body": stage_user_body,
        "items": arc_items,
    }

    # —— stage 2: AI analysis ——
    ai_body = str(causal_chain.get("ai_summary") or "").strip()
    if not ai_body:
        ai_body = _pick_str(
            concrete.get("analysis"),
            concrete.get("plan"),
            limit=1500,
        )
    if not ai_body and summary and mode != "fs_violation":
        ai_body = f"AI/系统形成的改动结论：{summary}"
    if not ai_body and mode == "fs_violation":
        ai_body = (
            "AI 按用户要求尝试改文件/代码；随后文件守卫（fs_guard）介入。"
            "详细过程见会话中用户口令之后的助手说明。"
        )
    stage_ai = {
        "key": "ai_analysis",
        "title": "2. AI 分析与处理",
        "body": ai_body or "未保存 AI 分析原文。",
        "items": [],
    }

    # —— stage 3: tools ——
    tool_items: list[dict[str, str]] = []
    seen_t: set[str] = set()
    for t in causal_chain.get("ai_tools") or []:
        if not isinstance(t, dict):
            continue
        tool = str(t.get("tool") or "?")
        args = str(t.get("args_summary") or "")[:300]
        key = f"{tool}|{args}"
        if key in seen_t:
            continue
        seen_t.add(key)
        tool_items.append({"tool": tool, "args_summary": args})
    for s in ui_steps:
        tool = str(s.get("tool") or "?")
        args = str(s.get("args_summary") or "")[:300]
        key = f"{tool}|{args}"
        if key in seen_t:
            continue
        seen_t.add(key)
        tool_items.append({"tool": tool, "args_summary": args})
    # evidence attempts (failed / tried)
    avoid_items: list[dict[str, str]] = []
    for a in procedure.get("avoid") or []:
        if isinstance(a, dict):
            avoid_items.append(
                {
                    "tool": str(a.get("tool") or "?"),
                    "args_summary": str(a.get("args_summary") or a.get("note") or "")[
                        :200
                    ],
                }
            )
    attempts = evidence.get("attempts") if isinstance(evidence, dict) else []
    attempt_n = len(attempts) if isinstance(attempts, list) else 0
    is_guard = mode == "fs_violation" or "回滚" in (summary or "")
    stage_tools = {
        "key": "tools",
        "title": "3. 调用工具与改动动作",
        "body": (
            "下列为会话/可复现步骤中记录的工具调用。"
            + (f" 过程中另有 {attempt_n} 次尝试记入证据。" if attempt_n else "")
        ),
        "items": tool_items[:30],
        "avoid": avoid_items[:8],
    }

    # —— stage 4: outcome ——
    if is_guard:
        outcome_title = "4. 改进结果（本次：拦截回滚）"
        outcome_body = (
            (what_happened or "文件守卫检出白名单外改动并回滚。")
            + "\n\n注意：页/代码上的改动未保留；本条作为流水线级改进点留档，"
            "提醒后续在合法路径修好根因。"
        )
    elif st == "accepted":
        outcome_title = "4. 改进结果（已生效）"
        before = _pick_str(concrete.get("before"), limit=400)
        after = _pick_str(concrete.get("after"), limit=400)
        bits = [what_happened or summary or headline]
        if before:
            bits.append(f"改前：{before}")
        if after and after != summary:
            bits.append(f"改后：{after}")
        verify = _pick_str(procedure.get("verify"), limit=400)
        if verify:
            bits.append(f"验收：{verify}")
        outcome_body = "\n".join(bits)
    elif st == "rejected":
        outcome_title = "4. 改进结果（已否决）"
        outcome_body = what_happened or summary or "本条已否决，不进入回放。"
    elif st == "conflict":
        outcome_title = "4. 改进结果（冲突待决）"
        outcome_body = what_happened or summary or "同位点存在多条候选，尚未留下唯一有效结果。"
    else:
        outcome_title = "4. 改进结果（尚未定稿）"
        outcome_body = what_happened or summary or headline or "仍在过程账中。"

    path_items = [{"text": p} for p in paths[:24]]
    stage_outcome = {
        "key": "outcome",
        "title": outcome_title,
        "body": outcome_body[:2500],
        "items": path_items,
        "paths_label": "涉及文件" if paths else "",
    }

    # —— stage 5: lifecycle next ——
    next_actions: list[str] = []
    if st in ("observed", "clustered", "candidate"):
        next_actions = [
            "在 Studio 确认本条是否「有效」",
            "确认后才会生成专书页补丁（L3）并参与同书回放",
        ]
    elif st == "conflict":
        next_actions = [
            "裁决：留下一条 / 否决本条",
            "留下后按有效改进进入后续层",
        ]
    elif st == "superseded":
        nxt = str(row.superseded_by or "")[:12]
        next_actions = [
            f"查阅替换结果{('（' + nxt + '…）') if nxt else ''}",
            "本条不再默认回放",
        ]
    elif st == "rejected":
        next_actions = ["无需后续动作（台账留存）"]
    elif st == "accepted":
        if authority == "pipeline" or is_guard:
            next_actions = [
                "在合法审批下修改流水线/核心代码（L0）",
                "修好后新书默认受益；不要指望页补丁抄到下一本",
                "本书仍可绑定方案（L2）作对照",
            ]
        elif pat == "none":
            next_actions = [
                "同书重导：按本条 procedure 回放页补丁（L3）",
                "若要推广到同类书：点「标成可复用草案」",
            ]
        elif pat == "draft":
            next_actions = [
                "等待/执行晋升：把草案落到教材族规则（L1）",
                "落地前仍可同书回放 L3",
            ]
        elif pat in ("approved", "landed", "verified"):
            next_actions = [
                "下一本默认吃教材族规则（L1）",
                "本书重导仍可用 L3 页补丁恢复",
            ]
        else:
            next_actions = [future_plan] if future_plan else []

    timeline = [
        f"创建：{str(row.created_at or '')[:19]}",
        f"更新：{str(row.updated_at or '')[:19]}",
        f"页码：p{page_show}" if page_show > 0 else "页码：未从对话解析到（不应再记成 p0）",
        f"当前状态：{status_label}（{status_meaning}）" if status_meaning else f"当前状态：{status_label}",
        f"复用标记：{pattern_label}",
        f"应用层：{layer_label}（{' / '.join(layers)}）",
    ]
    if trigger.get("job_id"):
        timeline.append(f"导入任务：{trigger['job_id']}")
    if trigger.get("session_id"):
        timeline.append(f"Copilot 会话：{trigger['session_id']}")
    if row.locus_key:
        timeline.append(f"问题位点：{row.locus_key}")
    if row.superseded_by:
        timeline.append(f"已被替换 → {str(row.superseded_by)[:16]}…")
    if row.merged_into_id:
        timeline.append(f"已合并 → {str(row.merged_into_id)[:16]}…")

    stage_life = {
        "key": "lifecycle",
        "title": "5. 后续生命周期",
        "body": future_plan,
        "status": st,
        "status_label": status_label,
        "status_meaning": status_meaning,
        "pattern_status": pat,
        "pattern_label": pattern_label,
        "apply_layers": layers,
        "apply_layer_label": layer_label,
        "next_actions": next_actions,
        "timeline": timeline,
        "items": [{"text": a} for a in next_actions],
    }

    # —— 改进历程：拆分/聚合来历（与「出生故事」分工）——
    lineage_events = _resolve_lineage(
        concrete if isinstance(concrete, dict) else {},
    )
    lineage_lines: list[str] = []
    lineage_items: list[dict[str, str]] = []
    for ev in lineage_events:
        lab = str(ev.get("summary") or "").strip()
        at = str(ev.get("at") or "").strip()[:19]
        if at and lab:
            lineage_lines.append(f"{at} · {lab}")
        elif lab:
            lineage_lines.append(lab)
        parent = ev.get("parent") if isinstance(ev.get("parent"), dict) else {}
        peers = ev.get("peers") if isinstance(ev.get("peers"), list) else []
        if parent and str(parent.get("summary") or "").strip():
            lineage_items.append(
                {
                    "text": f"父条：{str(parent.get('summary') or '')[:200]}",
                }
            )
        for peer in peers[:8]:
            if isinstance(peer, dict) and str(peer.get("summary") or "").strip():
                lineage_items.append(
                    {
                        "text": f"对立方：{str(peer.get('summary') or '')[:200]}",
                    }
                )
    if lineage_events:
        lineage_body = "本条在整理过程中经过拆分或聚合；下列为删旧条前留下的摘要。"
    else:
        lineage_body = "尚无拆分/聚合记录（本条未经拆分或聚合，或为改版前历史数据）。"
    stage_lineage = {
        "key": "lineage",
        "title": "改进历程（拆分 / 聚合）",
        "body": lineage_body,
        "timeline": lineage_lines,
        "items": lineage_items,
        "events": lineage_events,
    }

    return {
        "stages": [
            stage_user,
            stage_ai,
            stage_tools,
            stage_outcome,
            stage_lineage,
            stage_life,
        ],
        "status": st,
        "status_label": status_label,
        "next_actions": next_actions,
        "future_plan": future_plan,
        "lineage": lineage_events,
    }


def _ip_snapshot(row: CorpusImprovementPoint) -> dict[str, Any]:
    """删库前复印一条可读摘要，供改进历程展示（不保留完整 procedure 正文）。"""
    claim = loads(row.claim_json, {})
    concrete = loads(row.concrete_json, {})
    procedure = loads(row.procedure_json, {})
    if not isinstance(claim, dict):
        claim = {}
    if not isinstance(concrete, dict):
        concrete = {}
    if not isinstance(procedure, dict):
        procedure = {}
    summary = str(getattr(row, "summary", None) or claim.get("summary") or "").strip()
    steps = procedure.get("steps") if isinstance(procedure.get("steps"), list) else []
    return {
        "id": str(row.id or ""),
        "summary": summary[:400],
        "page_no": int(row.page_no or 0),
        "prep_status": _row_prep(row),
        "category": str(row.category or ""),
        "origin": str(getattr(row, "origin", None) or concrete.get("origin") or ""),
        "procedure_step_count": len(steps),
    }


def _append_lineage(
    concrete: dict[str, Any] | None, event: dict[str, Any]
) -> dict[str, Any]:
    """往 concrete.lineage 追加一条历程（append-only）。"""
    base = dict(concrete or {})
    lineage = base.get("lineage")
    events: list[Any] = list(lineage) if isinstance(lineage, list) else []
    events.append(dict(event or {}))
    base["lineage"] = events
    return base


def _resolve_lineage(
    concrete: dict[str, Any] | None,
    claim: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """读出历程列表；无完整记录但有 split_from 时做弱回填。"""
    c = concrete if isinstance(concrete, dict) else {}
    cl = claim if isinstance(claim, dict) else {}
    raw = c.get("lineage")
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for ev in raw:
            if isinstance(ev, dict) and str(ev.get("op") or "").strip():
                out.append(ev)
    if out:
        return out
    sf = str(c.get("split_from") or cl.get("split_from") or "").strip()
    if not sf:
        return []
    idx = c.get("split_index")
    if idx is None:
        idx = cl.get("split_index")
    meta: dict[str, Any] = {"split_from": sf, "weak": True}
    if idx is not None:
        try:
            meta["split_index"] = int(idx)
        except (TypeError, ValueError):
            meta["split_index"] = idx
    return [
        {
            "op": "split",
            "at": "",
            "actor": "system",
            "summary": f"拆自父条 {sf[:16]}…（父正文已不可用）",
            "parent": {
                "id": sf,
                "summary": "",
                "page_no": 0,
                "prep_status": "",
                "category": "",
                "origin": "",
                "procedure_step_count": 0,
            },
            "meta": meta,
            "weak": True,
        }
    ]


def _ip_out(row: CorpusImprovementPoint) -> dict[str, Any]:
    claim = loads(row.claim_json, {})
    evidence = loads(row.evidence_json, {})
    concrete = loads(row.concrete_json, {})
    if not isinstance(claim, dict):
        claim = {}
    if not isinstance(evidence, dict):
        evidence = {}
    if not isinstance(concrete, dict):
        concrete = {}
    summary = str(getattr(row, "summary", None) or claim.get("summary") or "")
    procedure = loads(row.procedure_json, {})
    if not isinstance(procedure, dict):
        procedure = {}
    origin, origin_label = _infer_origin(
        claim=claim,
        concrete=concrete,
        evidence=evidence,
        procedure=procedure,
        summary=summary,
        authority=str(row.authority or ""),
        session_id=str(row.source_session_id or ""),
        stored_origin=str(getattr(row, "origin", None) or ""),
    )
    # 改进点阶段一律专书页补丁；应用层只在抽象后的模式上判定，这里不再派生。
    layers, layer_label, future_plan = (
        ["L3"],
        "专书页补丁",
        "改进点均为专书页补丁；抽象成规律后再定 L1/L0 落地目标。",
    )
    display = _display_fields(
        row=row,
        claim=claim,
        concrete=concrete,
        evidence=evidence,
        procedure=procedure,
        summary=summary,
        authority=str(row.authority or ""),
    )
    lineage = _resolve_lineage(concrete, claim)
    return {
        "id": row.id,
        "fingerprint_hash": row.fingerprint_hash,
        "source_job_id": row.source_job_id,
        "source_session_id": row.source_session_id,
        "locus_key": row.locus_key,
        "page_no": row.page_no,
        "category": row.category,
        "authority": row.authority,
        "status": row.status,
        "claim": claim,
        "evidence": evidence,
        "procedure": procedure,
        "concrete": concrete,
        "pattern_id": row.pattern_id,
        "pattern_status": row.pattern_status,
        "superseded_by": row.superseded_by,
        "merged_into_id": row.merged_into_id,
        "origin": origin,
        "origin_label": origin_label,
        "summary": summary,
        "kind": str(getattr(row, "kind", None) or claim.get("kind") or ""),
        "needs_split": bool(int(getattr(row, "needs_split", 0) or 0)),
        "prep_status": _row_prep(row),
        "prep_status_label": prep_status_label(_row_prep(row)),
        # 需聚合簇（整理写入 concrete；列表分组用）
        "aggregate_group_id": str(concrete.get("aggregate_group_id") or "").strip(),
        "aggregate_reason": str(concrete.get("aggregate_reason") or "").strip()[:400],
        # 拆分/聚合来历（删旧条前写入的摘要历程）
        "lineage": lineage,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        # 兼容旧前端字段：固定为专书页补丁，不再按状态/抽象轨派生
        "apply_layers": layers,
        "apply_layer_label": layer_label,
        "future_plan": future_plan,
        **display,
        # List/UI: prefer dialogue page when stored page_no was wrongly 0
        "page_no": int(display.get("effective_page_no") or row.page_no or 0),
        # 一轮对话可能派生多条改进点：给 UI 一个折叠用的分组键
        "dialogue_key": _dialogue_key(
            page_no=int(display.get("effective_page_no") or row.page_no or 0),
            causal_chain=display.get("causal_chain") or {},
            session_id=str(row.source_session_id or ""),
            fallback_id=row.id,
        ),
        # 列表组头只做索引，用聚焦模板之后的正文；详情页仍保留完整原文
        "dialogue_lead": _dialogue_lead(str(display.get("user_input") or "")),
    }


def _dialogue_lead(user_input: str) -> str:
    """列表分组抬头用的一句话：去掉聚焦模板样板，只留用户真正说的话。"""
    raw = str(user_input or "").strip()
    if not raw:
        return ""
    free = _strip_focus_boilerplate(raw).strip()
    return (free or raw)[:160]


def _dialogue_key(
    *,
    page_no: int,
    causal_chain: dict[str, Any],
    session_id: str,
    fallback_id: str,
) -> str:
    """同一轮用户对话派生出的改进点共享一个键；没有对话线索时退回自身 id。"""
    seq = causal_chain.get("dialogue_seq") if isinstance(causal_chain, dict) else None
    if seq in (None, "", 0):
        return f"solo:{fallback_id}"
    return f"{session_id or 'na'}:p{page_no}:seq{seq}"


def _fingerprint_for_job(session: Session, job_id: str) -> str:
    job = session.get(CorpusImportJob, job_id) if job_id else None
    if job is None:
        return ""
    meta = loads(job.meta_json, {})
    bf = meta.get("book_fingerprint") or {}
    if isinstance(bf, dict) and bf.get("hash"):
        return str(bf["hash"])
    return str(meta.get("fingerprint_hash") or "")


def _add_link(
    session: Session, from_id: str, to_id: str, rel: str
) -> CorpusImprovementLink | None:
    if rel not in _REL_TYPES or not from_id or not to_id or from_id == to_id:
        return None
    row = CorpusImprovementLink(
        from_id=from_id, to_id=to_id, rel=rel, created_at=_now_iso()
    )
    session.add(row)
    return row


def _build_procedure_from_attempts(
    attempts: list[dict[str, Any]],
    *,
    verify: str = "user_confirm",
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    avoid: list[dict[str, Any]] = []
    # Last contiguous successful-looking attempts become steps; earlier → avoid
    for i, a in enumerate(attempts):
        step = {
            "tool": str(a.get("tool") or a.get("op") or "unknown"),
            "args_summary": str(a.get("args_summary") or a.get("summary") or "")[:300],
            "at": str(a.get("at") or ""),
            "attempt_id": str(a.get("id") or ""),
        }
        if i < max(0, len(attempts) - 3) and len(attempts) > 3:
            avoid.append({**step, "note": "earlier attempt (likely superseded)"})
        else:
            steps.append(step)
    if not steps and attempts:
        a = attempts[-1]
        steps = [
            {
                "tool": str(a.get("tool") or a.get("op") or "unknown"),
                "args_summary": str(a.get("args_summary") or a.get("summary") or "")[:300],
                "at": str(a.get("at") or ""),
                "attempt_id": str(a.get("id") or ""),
            }
        ]
    return {"steps": steps, "verify": verify, "avoid": avoid}


def has_success_procedure(
    procedure: dict[str, Any] | str | None = None,
    *,
    after_blocks: list[Any] | None = None,
) -> bool:
    """是否具备可复现的成功实现路径。

    条件（满足其一即可）：
    1. procedure.steps 中至少一步有真实工具名 + 非空说明
    2. 专书页补丁 after_json 非空（可回放的块级结果）
    """
    if after_blocks and isinstance(after_blocks, list) and len(after_blocks) > 0:
        return True
    proc: dict[str, Any]
    if isinstance(procedure, str):
        loaded = loads(procedure, {})
        proc = loaded if isinstance(loaded, dict) else {}
    elif isinstance(procedure, dict):
        proc = procedure
    else:
        proc = {}
    steps = proc.get("steps") if isinstance(proc.get("steps"), list) else []
    for st in steps:
        if not isinstance(st, dict):
            continue
        tool = str(st.get("tool") or "").strip()
        args = str(st.get("args_summary") or st.get("summary") or "").strip()
        if tool and tool not in ("unknown", "?") and args:
            return True
    return False


def purge_ips_without_success_procedure(session: Session) -> dict[str, Any]:
    """硬删所有缺少成功实现路径的改进点（及派生残留）。"""
    rows = session.query(CorpusImprovementPoint).all()
    purged_ids: list[str] = []
    details: list[dict[str, Any]] = []
    for r in rows:
        corr = (
            session.query(CorpusCorrection)
            .filter(CorpusCorrection.ip_id == r.id)
            .first()
        )
        after = loads(corr.after_json, []) if corr else []
        if not isinstance(after, list):
            after = []
        if has_success_procedure(r.procedure_json, after_blocks=after):
            continue
        out = purge_improvement_point(session, r.id)
        purged_ids.append(r.id)
        details.append(
            {
                "id": r.id,
                "page_no": int(r.page_no or 0),
                "summary": str(getattr(r, "summary", None) or "")[:160],
                "purged": out.get("purged"),
            }
        )
    return {
        "ok": True,
        "scanned": len(rows),
        "purged_count": len(purged_ids),
        "purged_ids": purged_ids,
        "details": details[:200],
    }


def ingest_attempts(
    session: Session,
    *,
    attempts: list[dict[str, Any]],
    job_id: str = "",
    session_id: str = "",
    fingerprint_hash: str = "",
) -> dict[str, Any]:
    """Upsert candidate IPs from layout_change_log-shaped attempts (cluster by locus)."""
    fp = (fingerprint_hash or "").strip() or _fingerprint_for_job(session, job_id)
    created = 0
    updated = 0
    now = _now_iso()
    for raw in attempts:
        if not isinstance(raw, dict):
            continue
        page_no = int(raw.get("page") or raw.get("page_no") or 0)
        summary = str(raw.get("summary") or "")
        kind = str(raw.get("kind") or "")
        op = str(raw.get("op") or raw.get("tool") or "")
        category = str(raw.get("category") or "") or infer_category(summary, kind, op)
        block_ids = raw.get("block_ids") or []
        if isinstance(block_ids, str):
            block_ids = [block_ids]
        if not isinstance(block_ids, list):
            block_ids = []
        locus = make_locus_key(
            page_no=page_no,
            category=category,
            block_ids=[str(x) for x in block_ids],
            slot=str(raw.get("slot") or ""),
        )
        existing = (
            session.query(CorpusImprovementPoint)
            .filter(
                CorpusImprovementPoint.source_job_id == (job_id or ""),
                CorpusImprovementPoint.locus_key == locus,
                CorpusImprovementPoint.status.in_(
                    ("observed", "clustered", "candidate", "conflict")
                ),
            )
            .order_by(CorpusImprovementPoint.updated_at.desc())
            .first()
        )
        attempt_entry = {
            "id": str(raw.get("id") or _sha1(f"{locus}:{summary}:{now}")),
            "at": str(raw.get("at") or now),
            "tool": str(raw.get("tool") or op),
            "op": op,
            "args_summary": str(raw.get("args_summary") or summary)[:400],
            "summary": summary[:400],
            "block_ids": [str(x) for x in block_ids],
        }
        ingest_origin = (
            str(raw.get("origin") or "").strip()
            or _SESSION_ORIGIN.get((session_id or "").strip())
            or ("pipeline_event" if str(raw.get("authority") or "") == "pipeline" else "")
            or "agent_page"
        )
        if existing is None:
            evidence = {"attempts": [attempt_entry]}
            row = CorpusImprovementPoint(
                fingerprint_hash=fp,
                source_job_id=job_id or "",
                source_session_id=session_id or "",
                locus_key=locus,
                page_no=page_no,
                category=category,
                authority=str(raw.get("authority") or "edition"),
                status="clustered",
                claim_json=dumps(
                    {
                        "summary": summary[:400],
                        "kind": kind,
                        "op": op,
                        "origin": ingest_origin,
                    }
                ),
                evidence_json=dumps(evidence),
                procedure_json="{}",
                concrete_json=dumps(
                    {
                        "scope": "block" if block_ids else "page",
                        "page_no": page_no,
                        "block_id": str(block_ids[0]) if block_ids else "",
                        "before": raw.get("before") or raw.get("before_hint") or "",
                        "after": raw.get("after") or raw.get("after_hint") or "",
                        "origin": ingest_origin,
                    }
                ),
                pattern_status="none",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            created += 1
        else:
            evidence = loads(existing.evidence_json, {})
            attempts_list = list(evidence.get("attempts") or [])
            attempts_list.append(attempt_entry)
            evidence["attempts"] = attempts_list[-40:]
            existing.evidence_json = dumps(evidence)
            prev_claim = loads(existing.claim_json, {})
            if not isinstance(prev_claim, dict):
                prev_claim = {}
            existing.claim_json = dumps(
                {
                    "summary": summary[:400],
                    "kind": kind,
                    "op": op,
                    "origin": str(prev_claim.get("origin") or "") or ingest_origin,
                }
            )
            existing.status = "candidate"
            existing.updated_at = now
            if fp and not existing.fingerprint_hash:
                existing.fingerprint_hash = fp
            updated += 1
    session.flush()
    return {"ok": True, "created": created, "updated": updated, "fingerprint_hash": fp}


def confirm_improvement(
    session: Session,
    *,
    job_id: str = "",
    session_id: str = "",
    fingerprint_hash: str = "",
    page_no: int = 0,
    category: str = "",
    summary: str = "",
    authority: str = "edition",
    kind: str = "",
    block_ids: list[str] | None = None,
    slot: str = "",
    procedure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    concrete: dict[str, Any] | None = None,
    pattern_abstract: str = "",
    pattern_status: str = "none",
    edition_family_id: str = "",
    write_correction: bool = True,
    origin: str = "",
    needs_split: bool | None = None,
) -> dict[str, Any]:
    """Mark a locus as accepted: require procedure, supersede peers, dual-track."""
    summary = normalize_direct_summary(summary)
    if attempts:
        ingest_attempts(
            session,
            attempts=attempts,
            job_id=job_id,
            session_id=session_id,
            fingerprint_hash=fingerprint_hash,
        )
    cat = category or infer_category(summary, kind, "")
    concrete_payload = dict(concrete) if isinstance(concrete, dict) else {}
    # Page must come from the user dialogue when caller left page_no=0
    page_no = _resolve_page_no(
        page_no=page_no,
        concrete=concrete_payload,
        block_ids=block_ids,
    )
    block_ids = list(block_ids or [])
    if not block_ids:
        bid = str(
            concrete_payload.get("block_id")
            or ((concrete_payload.get("causal_chain") or {}) if isinstance(concrete_payload.get("causal_chain"), dict) else {}).get("block_id")
            or ""
        ).strip()
        if bid:
            block_ids = [bid]
    slot_s = (slot or "").strip()
    cc0 = (
        concrete_payload.get("causal_chain")
        if isinstance(concrete_payload.get("causal_chain"), dict)
        else {}
    )
    if not slot_s:
        if cc0.get("dialogue_slot"):
            slot_s = str(cc0.get("dialogue_slot") or "")
        elif (authority or "").strip() == "pipeline" or str(
            concrete_payload.get("mode") or kind or ""
        ) == "fs_violation":
            # One dialogue → one IP (avoid collapsing all pipeline events onto page:0)
            import hashlib

            ui = str(
                concrete_payload.get("user_input")
                or cc0.get("user_input_display")
                or summary
                or ""
            )[:240]
            slot_s = "dlg:" + hashlib.sha1(
                f"{page_no}\n{ui}".encode("utf-8")
            ).hexdigest()[:10]
    locus = make_locus_key(
        page_no=page_no, category=cat, block_ids=block_ids, slot=slot_s
    )
    fp = (fingerprint_hash or "").strip() or _fingerprint_for_job(session, job_id)
    now = _now_iso()

    peers = (
        session.query(CorpusImprovementPoint)
        .filter(
            CorpusImprovementPoint.locus_key == locus,
            CorpusImprovementPoint.source_job_id == (job_id or ""),
        )
        .all()
    )
    # 同页同文但定位键不同的旧行（ingest 走 slot:page，confirm 走 slot:corr:<hash>）：
    # 不并进来就会变成同一改进的双写，台账里凭空多一条。
    peer_ids = {p.id for p in peers}
    twin_key = _dup_key(summary)
    if twin_key:
        for p in (
            session.query(CorpusImprovementPoint)
            .filter(
                CorpusImprovementPoint.source_job_id == (job_id or ""),
                CorpusImprovementPoint.page_no == int(page_no),
                CorpusImprovementPoint.status.in_(
                    ("observed", "clustered", "candidate", "conflict")
                ),
            )
            .all()
        ):
            if p.id in peer_ids:
                continue
            pc = loads(p.claim_json, {})
            if not isinstance(pc, dict):
                continue
            if _dup_key(str(pc.get("summary") or "")) == twin_key:
                peers.append(p)
                peer_ids.add(p.id)
    # Collect attempts from peers for procedure fallback
    all_attempts: list[dict[str, Any]] = []
    for p in peers:
        ev = loads(p.evidence_json, {})
        all_attempts.extend(ev.get("attempts") or [])
    if attempts:
        all_attempts.extend(attempts)

    proc = procedure if isinstance(procedure, dict) else None
    if not proc or not (proc.get("steps") or []):
        proc = _build_procedure_from_attempts(all_attempts, verify="user_confirm")
    after_hint = concrete_payload.get("after") if isinstance(concrete_payload, dict) else None
    after_blocks = after_hint if isinstance(after_hint, list) else None
    if not has_success_procedure(proc, after_blocks=after_blocks):
        raise ValueError(
            "accepted improvement requires a success procedure "
            "(procedure.steps with real tool+args, or non-empty after blocks)"
        )

    if not concrete_payload:
        concrete_payload = {
            "scope": "block" if block_ids else "page",
            "page_no": page_no,
            "block_id": str((block_ids or [""])[0] or ""),
            "before": "",
            "after": summary[:500],
        }
    else:
        concrete_payload["page_no"] = page_no
        if block_ids and not concrete_payload.get("block_id"):
            concrete_payload["block_id"] = block_ids[0]
    origin_raw = (origin or str(concrete_payload.get("origin") or "")).strip()
    if not origin_raw:
        if re.search(r"第\s*\d+\s*批", summary or "") or str(
            concrete_payload.get("source") or ""
        ) in ("ai_import", "ai_batch", "fullbook_ai"):
            origin_raw = "ai_auto"
        else:
            origin_raw = "user_ask"
    origin_s = normalize_origin(origin_raw) or "user_ask"
    concrete_payload["origin"] = origin_s
    # 管道事件不应进改进点表
    if (authority or "").strip() == "pipeline" and origin_raw == "pipeline_event":
        raise ValueError(
            "pipeline_event 不是页改进来源，请勿记入改进点；改流水线代码走独立通道"
        )

    # Keep narrative fields on both claim + concrete (UI + future migrate)
    claim_payload: dict[str, Any] = {
        "summary": summary[:400],
        "kind": kind,
        "origin": origin_s,
    }
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
        if concrete_payload.get(key) not in (None, "", []):
            claim_payload[key] = concrete_payload[key]
    # Also accept top-level attempts that carried paths/detail (migration)
    if attempts:
        for a in reversed(attempts):
            if not isinstance(a, dict):
                continue
            if not claim_payload.get("paths") and a.get("paths"):
                claim_payload["paths"] = a.get("paths")
                concrete_payload.setdefault("paths", a.get("paths"))
            if not claim_payload.get("detail") and a.get("detail"):
                claim_payload["detail"] = str(a.get("detail") or "")[:2000]
                concrete_payload.setdefault("detail", claim_payload["detail"])
            if not claim_payload.get("mode") and a.get("mode"):
                claim_payload["mode"] = str(a.get("mode") or "")
                concrete_payload.setdefault("mode", claim_payload["mode"])
            if not claim_payload.get("proposer") and a.get("proposer"):
                claim_payload["proposer"] = str(a.get("proposer") or "")
            if claim_payload.get("paths") and claim_payload.get("detail"):
                break

    # 只覆盖「同一改动事项」的旧行（摘要同义键相同），禁止按粗位点整页冲掉其它事项。
    matter_key = twin_key or _dup_key(summary)
    same_matter: list[CorpusImprovementPoint] = []
    for p in peers:
        if p.status not in (
            "accepted",
            "candidate",
            "clustered",
            "observed",
            "conflict",
            "effective",
        ):
            continue
        pc = loads(p.claim_json, {})
        p_sum = str(getattr(p, "summary", None) or (pc.get("summary") if isinstance(pc, dict) else "") or "")
        if matter_key and _dup_key(p_sum) == matter_key:
            same_matter.append(p)
            p.status = "superseded"
            p.updated_at = now

    # 默认不进抽象轨；只有显式 pattern_status=draft 才建模式。
    pat_status = (pattern_status or "none").strip().lower() or "none"
    if pat_status not in ("none", "draft"):
        pat_status = "none"
    # 入库一律待整理；分类由 Cursor Agent「整理」场景完成（不用 guess_needs_split 定终态）
    _ = needs_split  # 保留入参兼容，忽略
    initial_prep = PREP_NEEDS_TRIAGE

    row = CorpusImprovementPoint(
        fingerprint_hash=fp,
        source_job_id=job_id or "",
        source_session_id=session_id or "",
        locus_key=locus,
        page_no=int(page_no),
        category=cat,
        authority=(authority or "edition").strip() or "edition",
        status="accepted",
        claim_json=dumps(claim_payload),
        evidence_json=dumps(
            {
                "attempts": all_attempts[-40:],
                "attempt_ids": [str(a.get("id") or "") for a in all_attempts[-40:]],
            }
        ),
        procedure_json=dumps(proc),
        concrete_json=dumps(concrete_payload),
        pattern_status=pat_status,
        origin=origin_s,
        summary=(summary or "")[:2000],
        kind=(kind or "")[:64],
        needs_split=0,
        prep_status=initial_prep,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()

    for p in same_matter:
        p.superseded_by = row.id
        p.pattern_id = ""
        p.pattern_status = "none"
        _add_link(session, p.id, row.id, "supersedes")
        _add_link(session, p.id, row.id, "same_locus")

    pattern_id = ""
    if pat_status == "draft":
        if _row_prep(row) != PREP_READY:
            raise PrepGateError("只能对整理完成体（prep_status=ready）挂抽象轨")
        pattern_id = _ensure_pattern(
            session,
            category=cat,
            abstract_text=pattern_abstract or _abstract_summary(summary),
            procedure=proc,
            edition_family_id=edition_family_id,
            member_ip=row,
        )
        row.pattern_id = pattern_id
        row.pattern_status = "draft"

    corr_id = ""
    if write_correction and fp and row.authority != "pipeline":
        corr_id = _write_correction_from_ip(session, row, fp)

    # 不再在入库时自动标矛盾；待整理 → 人点「整理」→ Cursor Agent
    session.flush()
    return {
        "ok": True,
        "ip": _ip_out(row),
        "correction_id": corr_id,
        "pattern_id": pattern_id,
        "superseded_count": len(same_matter),
        "conflict_pairs": [],
    }


def _abstract_summary(summary: str) -> str:
    """Strip page numbers / figure ids for pattern track."""
    t = summary or ""
    t = re.sub(r"第?\s*\d+\s*页", "（页）", t)
    t = re.sub(r"\b\d+-\d+-\d+\b", "N-N-N", t)
    t = re.sub(r"p\.?\s*\d+", "pN", t, flags=re.I)
    return t[:400]


def recount_pattern(session: Session, pat: CorpusImprovementPattern) -> None:
    """从真实成员重算记账，并按跨书复现给出晋级建议。

    以前 ``member_count`` 是每次加成员 ``+= 1``。成员被拆走、改挂、删除时没人减
    回去，计数就和成员表对不上了，而晋级判断又依赖这个数。凡是改了成员关系的
    地方都该调这里，一次算清。
    """
    members = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.pattern_id == pat.id)
        .all()
    )
    accepted = [m for m in members if m.status == "accepted"]
    books = {m.fingerprint_hash for m in members if m.fingerprint_hash}
    symptom_fps: set[str] = set()
    for member in members:
        concrete = loads(member.concrete_json, {}) or {}
        fingerprint = str(concrete.get("symptom_fingerprint") or "")
        if fingerprint:
            symptom_fps.add(fingerprint)

    pat.member_count = len(members)
    pat.book_count = len(books)
    pat.fingerprints_json = dumps(sorted(symptom_fps))
    pat.updated_at = _now_iso()
    # 跨书复现或积累到足够页数，才值得占用人工定路由的注意力。
    if pat.status == "draft" and (len(books) >= 2 or len(accepted) >= _PATTERN_PROMOTE_PAGES):
        pat.status = "candidate"


def _ensure_pattern(
    session: Session,
    *,
    category: str,
    abstract_text: str,
    procedure: dict[str, Any],
    edition_family_id: str,
    member_ip: CorpusImprovementPoint,
    suspect_type: str = "",
) -> str:
    """找到或新建这条改进点该挂的模式。

    有症状名就按症状名归并——那是跨书对齐的锚点，两条描述得不一样但症状相同的
    改进点本来就该进同一个桶。没有症状名才退回摘要文本哈希：文本相似度做归并
    很脆（改一个字就分家），只当作还没抽象时的临时办法。
    """
    suspect = (suspect_type or "").strip().upper()
    if suspect:
        pat = (
            session.query(CorpusImprovementPattern)
            .filter(CorpusImprovementPattern.suspect_type == suspect)
            .filter(CorpusImprovementPattern.status.in_(PATTERN_MATCHABLE_STATUSES))
            .order_by(CorpusImprovementPattern.member_count.desc())
            .first()
        )
        if pat is not None:
            member_ip.pattern_id = pat.id
            session.flush()
            recount_pattern(session, pat)
            session.flush()
            _add_link(session, member_ip.id, pat.id, "same_pattern")
            return pat.id
    else:
        key = _sha1(f"{category}|{abstract_text[:120]}")
        rows = (
            session.query(CorpusImprovementPattern)
            .filter(CorpusImprovementPattern.category == category)
            .filter(CorpusImprovementPattern.status.in_(PATTERN_MATCHABLE_STATUSES))
            .all()
        )
        for pat in rows:
            if _sha1(f"{pat.category}|{(pat.abstract_text or '')[:120]}") != key:
                continue
            member_ip.pattern_id = pat.id
            session.flush()
            recount_pattern(session, pat)
            session.flush()
            _add_link(session, member_ip.id, pat.id, "same_pattern")
            return pat.id

    tmpl = {
        "steps": [
            {
                "tool": s.get("tool"),
                "args_summary": _abstract_summary(str(s.get("args_summary") or "")),
            }
            for s in (procedure.get("steps") or [])
        ],
        "verify": procedure.get("verify") or "final_html",
    }
    # 生效面留空：按类目关键词猜出来的先验从来没被确认过，却会让「已定路由」的
    # 统计看起来很好看。宁可空着等人或 Agent 判，也不要一个假答案。
    pat = CorpusImprovementPattern(
        category=category,
        abstract_text=abstract_text[:800],
        procedure_template_json=dumps(tmpl),
        target_layer="L1",
        landing_target="",
        landing_note="",
        suspect_type=suspect,
        edition_family_id=edition_family_id or "",
        status="draft",
        member_count=1,
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    session.add(pat)
    session.flush()
    member_ip.pattern_id = pat.id
    session.flush()
    recount_pattern(session, pat)
    _add_link(session, member_ip.id, pat.id, "same_pattern")
    return pat.id


def page_block_snapshot(
    session: Session, job_id: str, page_no: int
) -> list[dict[str, Any]]:
    """Current blocks of one page, as the replayable 'after' of a fix.

    At confirm time the job IR already holds the successful change, so the page
    itself is the most faithful result snapshot — far more reliable than asking
    the agent to hand back block payloads (which it usually skipped, leaving
    ``after`` empty and the patch unreplayable).
    """
    if not job_id or int(page_no or 0) <= 0:
        return []
    try:
        from app.services.document_ir import document_block_to_dict
        from app.services.import_ir_store import load_blocks

        job = session.get(CorpusImportJob, job_id)
        if job is None:
            return []
        blocks = load_blocks(job) or []
        page = sorted(
            (b for b in blocks if b.page_no == int(page_no)),
            key=lambda b: (b.order, b.id),
        )
        return [document_block_to_dict(b) for b in page]
    except Exception:  # noqa: BLE001
        logger.warning(
            "page snapshot failed job=%s page=%s", job_id, page_no, exc_info=True
        )
        return []


def _write_correction_from_ip(
    session: Session, ip: CorpusImprovementPoint, fingerprint_hash: str
) -> str:
    concrete = loads(ip.concrete_json, {})
    page_no = int(concrete.get("page_no") or ip.page_no or 0)
    block_id = str(concrete.get("block_id") or "")
    scope = str(concrete.get("scope") or ("block" if block_id else "page"))
    before = concrete.get("before")
    after = concrete.get("after")
    before_blocks = before if isinstance(before, list) else []
    after_blocks = after if isinstance(after, list) else []
    # Soft payload when only text hints exist
    refine = {
        "summary": loads(ip.claim_json, {}).get("summary"),
        "concrete": concrete,
        "from_ip": True,
    }
    if not after_blocks:
        snapshot = page_block_snapshot(session, ip.source_job_id, page_no)
        if snapshot:
            after_blocks = snapshot
            scope = "page"
            refine["after_source"] = "page_snapshot"
    now = _now_iso()
    row = CorpusCorrection(
        fingerprint_id="",
        fingerprint_hash=fingerprint_hash,
        scope=scope,
        page_no=page_no,
        block_id=block_id,
        user_note=str(loads(ip.claim_json, {}).get("summary") or "")[:500],
        before_json=dumps(before_blocks),
        after_json=dumps(after_blocks),
        refine_result_json=dumps(refine),
        source_job_id=ip.source_job_id,
        ip_id=ip.id,
        procedure_json=ip.procedure_json or "{}",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row.id


def converge_job(
    session: Session,
    *,
    job_id: str = "",
    session_id: str = "",
    fingerprint_hash: str = "",
) -> dict[str, Any]:
    """Converge all loci for a job: one accepted per locus when procedure exists.

    Returns conflicts that block finalize.
    """
    q = session.query(CorpusImprovementPoint)
    if job_id:
        q = q.filter(CorpusImprovementPoint.source_job_id == job_id)
    elif session_id:
        q = q.filter(CorpusImprovementPoint.source_session_id == session_id)
    else:
        raise ValueError("job_id or session_id required")
    if fingerprint_hash:
        q = q.filter(CorpusImprovementPoint.fingerprint_hash == fingerprint_hash)

    rows = q.all()
    by_locus: dict[str, list[CorpusImprovementPoint]] = {}
    for r in rows:
        by_locus.setdefault(r.locus_key or r.id, []).append(r)

    conflicts: list[dict[str, Any]] = []
    accepted_ids: list[str] = []
    now = _now_iso()

    for locus, group in by_locus.items():
        accepted = [g for g in group if g.status == "accepted"]
        conflicted = [g for g in group if g.status == "conflict"]
        if len(accepted) > 1:
            # Keep newest accepted; supersede older
            accepted.sort(key=lambda x: x.updated_at or x.created_at or "")
            winner = accepted[-1]
            for loser in accepted[:-1]:
                loser.status = "superseded"
                loser.superseded_by = winner.id
                loser.updated_at = now
                _add_link(session, loser.id, winner.id, "supersedes")
            accepted_ids.append(winner.id)
            continue
        if accepted:
            accepted_ids.append(accepted[0].id)
            continue
        if conflicted:
            conflicts.append(
                {
                    "locus_key": locus,
                    "ip_ids": [c.id for c in conflicted],
                    "page_no": conflicted[0].page_no,
                }
            )
            continue
        # Auto-accept newest candidate/clustered if procedure can be built
        open_rows = [
            g
            for g in group
            if g.status in ("candidate", "clustered", "observed")
        ]
        if not open_rows:
            continue
        open_rows.sort(key=lambda x: x.updated_at or x.created_at or "")
        winner = open_rows[-1]
        ev = loads(winner.evidence_json, {})
        proc = loads(winner.procedure_json, {})
        if not (proc.get("steps") or []):
            proc = _build_procedure_from_attempts(list(ev.get("attempts") or []))
        if not proc.get("steps"):
            conflicts.append(
                {
                    "locus_key": locus,
                    "ip_ids": [winner.id],
                    "page_no": winner.page_no,
                    "reason": "missing_procedure",
                }
            )
            winner.status = "conflict"
            winner.updated_at = now
            continue
        for loser in open_rows[:-1]:
            loser.status = "superseded"
            loser.superseded_by = winner.id
            loser.updated_at = now
            _add_link(session, loser.id, winner.id, "supersedes")
        winner.status = "accepted"
        winner.procedure_json = dumps(proc)
        winner.updated_at = now
        accepted_ids.append(winner.id)
        if winner.fingerprint_hash and winner.authority != "pipeline":
            # Avoid duplicate corrections for same ip
            existing = (
                session.query(CorpusCorrection)
                .filter(CorpusCorrection.ip_id == winner.id)
                .first()
            )
            if existing is None:
                _write_correction_from_ip(session, winner, winner.fingerprint_hash)

    session.flush()
    return {
        "ok": len(conflicts) == 0,
        "accepted_count": len(accepted_ids),
        "accepted_ids": accepted_ids,
        "conflicts": conflicts,
        "locus_count": len(by_locus),
    }


def _queue_row_refs_ip(row: EditionCorrectionQueue, ip_id: str) -> bool:
    if str(row.source_ref or "") == ip_id:
        return True
    raw = row.candidate_json or ""
    if ip_id not in raw:
        return False
    # 结构化确认，避免 UUID 子串误伤
    for needle in (
        f'"ip_id": "{ip_id}"',
        f'"ip_id":"{ip_id}"',
        f'"source_ref": "{ip_id}"',
        f'"source_ref":"{ip_id}"',
    ):
        if needle in raw:
            return True
    cand = loads(raw, {})
    if not isinstance(cand, dict):
        return False
    if str(cand.get("ip_id") or "") == ip_id:
        return True
    nested = cand.get("candidate")
    if isinstance(nested, dict) and str(nested.get("ip_id") or "") == ip_id:
        return True
    return False


def purge_improvement_point(session: Session, ip_id: str) -> dict[str, Any]:
    """彻底删除一条改进点及其派生残留（硬删，无 soft-delete）。

    清理范围：
    - corpus_improvement_points 本行
    - corpus_corrections（专书页补丁，ip_id 匹配）
    - corpus_improvement_links（from/to 任一端）
    - edition_correction_queue（source_ref / candidate 引用）
    - 其它 IP 上指向本行的 superseded_by / merged_into_id
    - 所属模式：重算 member_count；若已无成员则删除空模式及其链接
    """
    ip_id = (ip_id or "").strip()
    if not ip_id:
        raise ValueError("ip_id required")
    row = session.get(CorpusImprovementPoint, ip_id)
    if row is None:
        raise ValueError(f"ip not found: {ip_id}")

    pattern_id = str(row.pattern_id or "").strip()
    counts = {
        "ip_id": ip_id,
        "page_no": int(row.page_no or 0),
        "summary": str(getattr(row, "summary", None) or "")[:200],
        "corrections": 0,
        "links": 0,
        "edition_queue": 0,
        "cleared_superseded_by": 0,
        "cleared_merged_into": 0,
        "patterns_deleted": 0,
        "pattern_members_left": None,
    }

    for c in (
        session.query(CorpusCorrection).filter(CorpusCorrection.ip_id == ip_id).all()
    ):
        session.delete(c)
        counts["corrections"] += 1

    eq_hits: list[EditionCorrectionQueue] = []
    seen_eq: set[str] = set()
    for r in (
        session.query(EditionCorrectionQueue)
        .filter(EditionCorrectionQueue.source_ref == ip_id)
        .all()
    ):
        eq_hits.append(r)
        seen_eq.add(r.id)
    # candidate_json 可能带 ip_id；用 LIKE 缩小扫描再结构化确认
    for r in (
        session.query(EditionCorrectionQueue)
        .filter(EditionCorrectionQueue.candidate_json.like(f"%{ip_id}%"))
        .all()
    ):
        if r.id in seen_eq:
            continue
        if _queue_row_refs_ip(r, ip_id):
            eq_hits.append(r)
            seen_eq.add(r.id)
    for r in eq_hits:
        session.delete(r)
        counts["edition_queue"] += 1

    for p in (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.superseded_by == ip_id)
        .all()
    ):
        p.superseded_by = ""
        counts["cleared_superseded_by"] += 1
    for p in (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.merged_into_id == ip_id)
        .all()
    ):
        p.merged_into_id = ""
        counts["cleared_merged_into"] += 1

    for link in (
        session.query(CorpusImprovementLink)
        .filter(
            (CorpusImprovementLink.from_id == ip_id)
            | (CorpusImprovementLink.to_id == ip_id)
        )
        .all()
    ):
        session.delete(link)
        counts["links"] += 1

    session.delete(row)
    session.flush()

    if pattern_id:
        left = (
            session.query(CorpusImprovementPoint)
            .filter(CorpusImprovementPoint.pattern_id == pattern_id)
            .count()
        )
        counts["pattern_members_left"] = left
        pat = session.get(CorpusImprovementPattern, pattern_id)
        if pat is not None:
            if left == 0:
                for link in (
                    session.query(CorpusImprovementLink)
                    .filter(
                        (CorpusImprovementLink.from_id == pattern_id)
                        | (CorpusImprovementLink.to_id == pattern_id)
                    )
                    .all()
                ):
                    session.delete(link)
                    counts["links"] += 1
                session.delete(pat)
                counts["patterns_deleted"] = 1
            else:
                pat.member_count = int(left)
                pat.updated_at = _now_iso()

    session.flush()
    return {"ok": True, "purged": counts}


def resolve_conflict(
    session: Session, ip_id: str, *, winner: bool = True
) -> dict[str, Any]:
    row = session.get(CorpusImprovementPoint, ip_id)
    if row is None:
        raise ValueError(f"ip not found: {ip_id}")
    now = _now_iso()
    peers = (
        session.query(CorpusImprovementPoint)
        .filter(
            CorpusImprovementPoint.locus_key == row.locus_key,
            CorpusImprovementPoint.source_job_id == row.source_job_id,
            CorpusImprovementPoint.id != row.id,
        )
        .all()
    )
    if winner:
        proc = loads(row.procedure_json, {})
        if not (proc.get("steps") or []):
            ev = loads(row.evidence_json, {})
            proc = _build_procedure_from_attempts(list(ev.get("attempts") or []))
        if not proc.get("steps"):
            raise ValueError("cannot accept winner without procedure.steps")
        row.procedure_json = dumps(proc)
        row.status = "accepted"
        row.updated_at = now
        for p in peers:
            if p.status in ("accepted", "conflict", "candidate"):
                p.status = "superseded"
                p.superseded_by = row.id
                p.updated_at = now
                _add_link(session, p.id, row.id, "supersedes")
        if row.fingerprint_hash and row.authority != "pipeline":
            if (
                session.query(CorpusCorrection)
                .filter(CorpusCorrection.ip_id == row.id)
                .first()
                is None
            ):
                _write_correction_from_ip(session, row, row.fingerprint_hash)
    else:
        row.status = "rejected"
        row.updated_at = now
    session.flush()
    return {"ok": True, "ip": _ip_out(row)}


def set_pattern(
    session: Session,
    ip_id: str,
    *,
    pattern_status: str = "none",
    abstract_text: str = "",
    target_layer: str = "",
    edition_family_id: str = "",
    suspect_type: str = "",
) -> dict[str, Any]:
    row = session.get(CorpusImprovementPoint, ip_id)
    if row is None:
        raise ValueError(f"ip not found: {ip_id}")
    if row.status != "accepted":
        raise ValueError("pattern track only for accepted IPs")
    now = _now_iso()
    status = (pattern_status or "none").strip() or "none"
    row.pattern_status = status
    if status == "none":
        row.pattern_id = ""
        row.updated_at = now
        session.flush()
        return {"ok": True, "ip": _ip_out(row)}
    if _row_prep(row) != PREP_READY:
        raise PrepGateError(
            f"只能对整理完成体挂抽象轨（当前 prep_status={_row_prep(row)}）"
        )
    proc = loads(row.procedure_json, {})
    claim = loads(row.claim_json, {})
    pid = _ensure_pattern(
        session,
        category=row.category,
        abstract_text=abstract_text or _abstract_summary(str(claim.get("summary") or "")),
        procedure=proc,
        edition_family_id=edition_family_id,
        member_ip=row,
        suspect_type=suspect_type,
    )
    row.pattern_id = pid
    row.pattern_status = "draft" if status == "draft" else status
    pat = session.get(CorpusImprovementPattern, pid)
    if pat and target_layer in ("L0", "L1", "L2"):
        pat.target_layer = target_layer
        pat.updated_at = now
    row.updated_at = now
    session.flush()
    return {"ok": True, "ip": _ip_out(row), "pattern_id": pid}


# ---------------------------------------------------------------- 模式级操作
# 抽象轨的三步：列出（看清有什么）→ 定路由（决定写到哪个生效面）→ 落地（回写
# landed_ref）。真正的写盘由 buddy-copilot 走 fs.request_edit / editions.publish
# 批准闸门完成，这里只记账 —— 服务端不自己改工作区文件。


def _resolved_playbook(pat: CorpusImprovementPattern) -> str:
    """手册步骤全文。真身在检测器 YAML 里，读的时候解析，改完即时生效。"""
    try:
        # 延迟导入：corpus_symptoms 依赖本模块的 LANDING_TARGETS，顶层会成环。
        from app.services.corpus_symptoms import resolve_playbook

        return resolve_playbook(
            pat.playbook_ref or "", suspect_type=pat.suspect_type or ""
        )
    except Exception:  # noqa: BLE001 — 读不到手册不该让模式列表报错
        return ""


def _pattern_out(
    session: Session, pat: CorpusImprovementPattern, *, with_members: bool = False
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": pat.id,
        "category": pat.category,
        "abstract_text": pat.abstract_text,
        "procedure_template": loads(pat.procedure_template_json, {}),
        "target_layer": pat.target_layer,
        "landing_target": pat.landing_target or "",
        "landing_target_label": LANDING_TARGETS.get(pat.landing_target or "", ""),
        "landing_note": pat.landing_note or "",
        "edition_family_id": pat.edition_family_id,
        "status": pat.status,
        "member_count": int(pat.member_count or 0),
        "landed_ref": pat.landed_ref or "",
        # 映射面：有 suspect_type 才可能在新书里被认出来，有 detector_ref 才查得出。
        "suspect_type": pat.suspect_type or "",
        "detector_ref": pat.detector_ref or "",
        "remediation_kind": pat.remediation_kind or "",
        # playbook_ref 是指针（出处），playbook 是按指针解析出来的步骤全文。
        "playbook_ref": pat.playbook_ref or "",
        "playbook": _resolved_playbook(pat),
        "fingerprints": loads(pat.fingerprints_json, []) or [],
        "book_count": int(pat.book_count or 0),
        "hits": int(pat.hits or 0),
        "true_positive": int(pat.true_positive or 0),
        "false_positive": int(pat.false_positive or 0),
        "created_at": pat.created_at,
        "updated_at": pat.updated_at,
    }
    if with_members:
        members = (
            session.query(CorpusImprovementPoint)
            .filter(CorpusImprovementPoint.pattern_id == pat.id)
            .order_by(CorpusImprovementPoint.page_no)
            .all()
        )
        out["members"] = [
            {
                "ip_id": m.id,
                "page_no": m.page_no,
                "job_id": m.source_job_id,
                "fingerprint_hash": m.fingerprint_hash,
                "summary": str(loads(m.claim_json, {}).get("summary") or ""),
            }
            for m in members
        ]
        # 成员页就是这条模式的黄金集：落地后这些页应当改善，其余页不得变差。
        out["member_pages"] = sorted({int(m.page_no or 0) for m in members if m.page_no})
    return out


def list_patterns(
    session: Session,
    *,
    category: str = "",
    status: str = "",
    landing_target: str = "",
    min_members: int = 0,
    with_members: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    q = session.query(CorpusImprovementPattern)
    if category:
        q = q.filter(CorpusImprovementPattern.category == category)
    if status:
        q = q.filter(CorpusImprovementPattern.status == status)
    if landing_target:
        q = q.filter(CorpusImprovementPattern.landing_target == landing_target)
    if min_members > 0:
        q = q.filter(CorpusImprovementPattern.member_count >= min_members)
    rows = (
        q.order_by(
            CorpusImprovementPattern.member_count.desc(),
            CorpusImprovementPattern.updated_at.desc(),
        )
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    by_target: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in session.query(CorpusImprovementPattern).all():
        by_target[r.landing_target or "(未定路由)"] = (
            by_target.get(r.landing_target or "(未定路由)", 0) + 1
        )
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "patterns": [_pattern_out(session, r, with_members=with_members) for r in rows],
        "counts": {"by_landing_target": by_target, "by_status": by_status},
        "landing_targets": LANDING_TARGETS,
    }


def get_pattern(session: Session, pattern_id: str) -> dict[str, Any]:
    pat = session.get(CorpusImprovementPattern, pattern_id)
    if pat is None:
        raise ValueError(f"pattern not found: {pattern_id}")
    return _pattern_out(session, pat, with_members=True)


def route_pattern(
    session: Session,
    pattern_id: str,
    *,
    landing_target: str,
    note: str = "",
) -> dict[str, Any]:
    """Decide which effective surface this pattern should be written to."""
    pat = session.get(CorpusImprovementPattern, pattern_id)
    if pat is None:
        raise ValueError(f"pattern not found: {pattern_id}")
    target = (landing_target or "").strip()
    if target not in LANDING_TARGETS:
        raise ValueError(
            f"unknown landing_target: {target!r}; expected one of {sorted(LANDING_TARGETS)}"
        )
    pat.landing_target = target
    pat.landing_note = (note or "")[:2000]
    pat.target_layer = target.split(".", 1)[0]
    # 判为不可规则化的直接进终态，不再占用待落地清单。
    pat.status = "no_landing" if target == "L3.book_patch" else "routed"
    pat.updated_at = _now_iso()
    session.flush()
    return {"ok": True, "pattern": _pattern_out(session, pat, with_members=True)}


def land_pattern(
    session: Session,
    pattern_id: str,
    *,
    landed_ref: str,
    note: str = "",
) -> dict[str, Any]:
    """Record that the pattern has been written to its effective surface.

    ``landed_ref`` points at the concrete artifact - a layout rule id, a
    ``config.json`` key path, a CSS selector, or a test node id - so the pattern
    can be traced back from the file that now implements it.
    """
    pat = session.get(CorpusImprovementPattern, pattern_id)
    if pat is None:
        raise ValueError(f"pattern not found: {pattern_id}")
    if not pat.landing_target:
        raise ValueError("pattern has no landing_target; call route first")
    if pat.landing_target == "L3.book_patch":
        raise ValueError("L3.book_patch patterns stay as book patches and do not land")
    ref = (landed_ref or "").strip()
    if not ref:
        raise ValueError("landed_ref required")
    pat.landed_ref = ref[:512]
    pat.status = "landed"
    if note:
        pat.landing_note = (note or "")[:2000]
    pat.updated_at = _now_iso()
    session.flush()
    return {"ok": True, "pattern": _pattern_out(session, pat, with_members=True)}


def merge_patterns(
    session: Session, *, keep_id: str, merge_ids: list[str], note: str = ""
) -> dict[str, Any]:
    """Fold duplicate patterns into one, repointing their member IPs."""
    keep = session.get(CorpusImprovementPattern, keep_id)
    if keep is None:
        raise ValueError(f"pattern not found: {keep_id}")
    moved = 0
    dropped: list[str] = []
    for pid in merge_ids:
        if pid == keep_id:
            continue
        src = session.get(CorpusImprovementPattern, pid)
        if src is None:
            continue
        for ip in (
            session.query(CorpusImprovementPoint)
            .filter(CorpusImprovementPoint.pattern_id == pid)
            .all()
        ):
            ip.pattern_id = keep_id
            ip.updated_at = _now_iso()
            _add_link(session, ip.id, keep_id, "same_pattern")
            moved += 1
        session.delete(src)
        dropped.append(pid)
    session.flush()
    recount_pattern(session, keep)
    if note:
        keep.landing_note = (note or "")[:2000]
    session.flush()
    return {
        "ok": True,
        "pattern": _pattern_out(session, keep, with_members=True),
        "merged_ids": dropped,
        "moved_members": moved,
    }


def split_pattern(
    session: Session,
    *,
    pattern_id: str,
    member_ip_ids: list[str],
    abstract_text: str,
    category: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Move some members out into a new pattern.

    The inverse of :func:`merge_patterns`, for when one bucket has quietly
    conflated two different regularities.
    """
    src = session.get(CorpusImprovementPattern, pattern_id)
    if src is None:
        raise ValueError(f"pattern not found: {pattern_id}")
    wanted = [i for i in (member_ip_ids or []) if i]
    if not wanted:
        raise ValueError("member_ip_ids required")
    members = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.pattern_id == pattern_id)
        .filter(CorpusImprovementPoint.id.in_(wanted))
        .all()
    )
    if not members:
        raise ValueError("none of the given IPs belong to this pattern")
    if len(members) >= int(src.member_count or 0):
        raise ValueError("cannot split out every member; use route/merge instead")
    cat = (category or src.category or "").strip()
    now = _now_iso()
    new_pat = CorpusImprovementPattern(
        category=cat,
        abstract_text=(abstract_text or src.abstract_text or "")[:800],
        procedure_template_json=src.procedure_template_json,
        target_layer="L1",
        landing_target="",
        landing_note=(note or "从 %s 拆出" % pattern_id)[:2000],
        edition_family_id=src.edition_family_id,
        status="draft",
        member_count=len(members),
        created_at=now,
        updated_at=now,
    )
    session.add(new_pat)
    session.flush()
    for ip in members:
        ip.pattern_id = new_pat.id
        ip.updated_at = now
        _add_link(session, ip.id, new_pat.id, "same_pattern")
    session.flush()
    recount_pattern(session, src)
    recount_pattern(session, new_pat)
    session.flush()
    return {
        "ok": True,
        "source": _pattern_out(session, src, with_members=True),
        "created": _pattern_out(session, new_pat, with_members=True),
    }


def _parent_after_blocks(session: Session, row: CorpusImprovementPoint) -> list[Any]:
    concrete = loads(row.concrete_json, {})
    if not isinstance(concrete, dict):
        concrete = {}
    after = concrete.get("after")
    if isinstance(after, list) and after:
        return after
    corr = (
        session.query(CorpusCorrection)
        .filter(CorpusCorrection.ip_id == row.id)
        .first()
    )
    if corr is None:
        return []
    loaded = loads(corr.after_json, [])
    return loaded if isinstance(loaded, list) else []


def _after_index_list(after_blocks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, blk in enumerate(after_blocks):
        if not isinstance(blk, dict):
            out.append({"index": i, "block_id": "", "preview": str(blk)[:80]})
            continue
        bid = str(blk.get("id") or blk.get("block_id") or "")
        text = str(blk.get("text") or blk.get("content") or blk.get("html") or "")[:80]
        out.append({"index": i, "block_id": bid, "preview": text})
    return out


def _step_alone_ok(step: Any) -> bool:
    if not isinstance(step, dict):
        return False
    return has_success_procedure({"steps": [step]})


def _summary_is_multi_topic(summary: str) -> bool:
    """摘要是否记了多件事（判定能否标待拆分）。

    分号列举两段即可；顿号列举容易出现在单一主题内（如「删A、B两图」），
    因此要求三段以上才算多主题。
    """
    text = str(summary or "")
    if len([p for p in re.split(r"[；;]", text) if p.strip()]) >= 2:
        return True
    return len([p for p in re.split(r"[、,，]", text) if p.strip()]) >= 3


def assemble_split_children(
    session: Session,
    ip_id: str,
    *,
    children_spec: list[dict[str, Any]],
) -> dict[str, Any]:
    """按 step_indices / after_indices 组装完备子条提案（不写库）。"""
    ip_id = (ip_id or "").strip()
    row = session.get(CorpusImprovementPoint, ip_id)
    if row is None:
        raise ValueError(f"ip not found: {ip_id}")
    proc = loads(row.procedure_json, {})
    if not isinstance(proc, dict):
        proc = {}
    steps = list(proc.get("steps") or [])
    after_blocks = _parent_after_blocks(session, row)
    used_after: set[int] = set()
    proposals: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, ch in enumerate(children_spec):
        if not isinstance(ch, dict):
            errors.append(f"子条 {i + 1} 不是对象")
            continue
        sum_i = normalize_direct_summary(str(ch.get("summary") or "").strip())
        if not sum_i:
            errors.append(f"子条 {i + 1} 缺少 summary")
            continue
        raw_idx = ch.get("step_indices")
        if not isinstance(raw_idx, list) or not raw_idx:
            errors.append(f"子条 {i + 1} 缺少 step_indices")
            continue
        step_indices: list[int] = []
        for x in raw_idx:
            try:
                si = int(x)
            except (TypeError, ValueError):
                errors.append(f"子条 {i + 1} step_indices 非法")
                step_indices = []
                break
            if si < 0 or si >= len(steps):
                errors.append(f"子条 {i + 1} step_index {si} 越界")
                step_indices = []
                break
            step_indices.append(si)
        if not step_indices:
            continue
        slice_steps = [steps[si] for si in step_indices]
        proc_i = {
            **{k: v for k, v in proc.items() if k != "steps"},
            "steps": slice_steps,
            "split_from": ip_id,
            "split_index": i,
            "step_indices": step_indices,
        }
        if not has_success_procedure(proc_i):
            errors.append(f"子条 {i + 1} procedure 不完备（需真实 tool+args）")
            continue
        after_idx: list[int] = []
        for x in ch.get("after_indices") or []:
            try:
                ai = int(x)
            except (TypeError, ValueError):
                errors.append(f"子条 {i + 1} after_indices 非法")
                after_idx = []
                break
            if ai < 0 or ai >= len(after_blocks):
                errors.append(f"子条 {i + 1} after_index {ai} 越界")
                after_idx = []
                break
            if ai in used_after:
                errors.append(f"after 块 {ai} 被多个子条占用")
                after_idx = []
                break
            after_idx.append(ai)
        for ai in after_idx:
            used_after.add(ai)
        child_after = [after_blocks[ai] for ai in after_idx]
        proposals.append(
            {
                "summary": sum_i[:2000],
                "category": str(ch.get("category") or "").strip()
                or infer_category(sum_i, str(getattr(row, "kind", None) or ""), ""),
                "step_indices": step_indices,
                "after_indices": after_idx,
                "procedure_slice": proc_i,
                "after": child_after,
            }
        )

    unassigned = [i for i in range(len(after_blocks)) if i not in used_after]
    return {
        "ok": len(errors) == 0 and len(proposals) >= 2 and not unassigned,
        "source_id": ip_id,
        "source_summary": str(getattr(row, "summary", None) or ""),
        "prep_status": _row_prep(row),
        "steps": steps,
        "after_index": _after_index_list(after_blocks),
        "proposals": proposals,
        "unassigned_after_indices": unassigned,
        "errors": errors,
        "needs_agent": False,
    }


def split_propose(
    session: Session,
    ip_id: str,
    *,
    children_spec: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """拆分提案（不写库）。

    - 若传入 children_spec（含 step_indices/after_indices）：组装完备路径
    - 否则尝试 1:1 steps 规则快路径；不满足则 needs_agent=true 供 Cursor Agent
    """
    ip_id = (ip_id or "").strip()
    row = session.get(CorpusImprovementPoint, ip_id)
    if row is None:
        raise ValueError(f"ip not found: {ip_id}")
    if children_spec:
        return assemble_split_children(session, ip_id, children_spec=children_spec)

    proc = loads(row.procedure_json, {})
    if not isinstance(proc, dict):
        proc = {}
    steps = [s for s in (proc.get("steps") or []) if isinstance(s, dict)]
    after_blocks = _parent_after_blocks(session, row)
    summary = str(getattr(row, "summary", None) or "")
    parts = [p.strip() for p in re.split(r"[；;]", summary) if p.strip()]

    # 窄规则：每步单独完备 且 摘要段数=步数
    if (
        len(steps) >= 2
        and all(_step_alone_ok(s) for s in steps)
        and len(parts) == len(steps)
    ):
        spec = [
            {
                "summary": parts[i],
                "step_indices": [i],
                "after_indices": [],
                "category": infer_category(parts[i]),
            }
            for i in range(len(steps))
        ]
        # 尝试按 block_id 挂 after
        for i, step in enumerate(steps):
            blob = str(step.get("args_summary") or "") + str(step.get("summary") or "")
            for ai, blk in enumerate(after_blocks):
                if not isinstance(blk, dict):
                    continue
                bid = str(blk.get("id") or blk.get("block_id") or "")
                if bid and bid in blob:
                    if ai not in {
                        x
                        for sp in spec
                        for x in (sp.get("after_indices") or [])
                    }:
                        spec[i]["after_indices"] = list(spec[i].get("after_indices") or []) + [
                            ai
                        ]
        assembled = assemble_split_children(session, ip_id, children_spec=spec)
        if assembled.get("ok"):
            assembled["mode"] = "rule_fast_path"
            return assembled

    return {
        "ok": False,
        "needs_agent": True,
        "source_id": ip_id,
        "source_summary": summary,
        "prep_status": _row_prep(row),
        "steps": steps,
        "after_index": _after_index_list(after_blocks),
        "proposals": [],
        "unassigned_after_indices": list(range(len(after_blocks))),
        "errors": ["规则快路径不适用；请用 Cursor Agent（ip_split）提交 step_indices"],
        "mode": "needs_agent",
    }


def _validate_split_children(
    session: Session,
    row: CorpusImprovementPoint,
    children: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """硬闸：每子条完备 procedure；after 互不重叠且全部覆盖。"""
    if len(children) < 2:
        raise ValueError("拆分至少需要 2 条子条")
    after_blocks = _parent_after_blocks(session, row)
    used_after: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for i, ch in enumerate(children):
        if not isinstance(ch, dict):
            raise ValueError(f"子条 {i + 1} 非法")
        sum_i = normalize_direct_summary(str(ch.get("summary") or "").strip())
        if not sum_i:
            raise ValueError(f"子条 {i + 1} 缺少 summary")
        proc_i = ch.get("procedure_slice") or ch.get("procedure")
        if not isinstance(proc_i, dict) or not has_success_procedure(proc_i):
            raise ValueError(
                f"子条 {i + 1} 缺少完备成功路径（禁止复制父 procedure 整包）"
            )
        after_list = ch.get("after")
        after_idx = ch.get("after_indices")
        child_after: list[Any] = []
        # 有父 after 时必须以 after_indices 做互不重叠 + 全覆盖闸（after 列表只作内容）
        if after_blocks:
            if not isinstance(after_idx, list):
                raise ValueError(f"子条 {i + 1} 缺少 after_indices")
            idxs: list[int] = []
            for x in after_idx:
                ai = int(x)
                if ai < 0 or ai >= len(after_blocks):
                    raise ValueError(f"子条 {i + 1} after_index {ai} 越界")
                if ai in used_after:
                    raise ValueError(f"after 块 {ai} 重叠分配")
                used_after.add(ai)
                idxs.append(ai)
            if (
                isinstance(after_list, list)
                and after_list
                and len(after_list) == len(idxs)
            ):
                child_after = list(after_list)
            else:
                child_after = [after_blocks[ai] for ai in idxs]
        elif isinstance(after_list, list):
            child_after = list(after_list)
        normalized.append(
            {
                **ch,
                "summary": sum_i,
                "procedure_slice": proc_i,
                "after": child_after,
            }
        )
    unassigned = [i for i in range(len(after_blocks)) if i not in used_after]
    if after_blocks and unassigned:
        raise ValueError(
            f"仍有未分配 after 块: {unassigned}（须全部分完再确认拆分）"
        )
    return normalized


def split_apply(
    session: Session,
    ip_id: str,
    *,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """人确认拆分：建 N 条新 IP（完备路径硬闸），再 purge 旧条。"""
    ip_id = (ip_id or "").strip()
    row = session.get(CorpusImprovementPoint, ip_id)
    if row is None:
        raise ValueError(f"ip not found: {ip_id}")
    if _row_prep(row) != PREP_NEEDS_SPLIT and not int(getattr(row, "needs_split", 0) or 0):
        raise ValueError("仅待拆分（prep_status=needs_split）可确认拆分")
    if not children:
        raise ValueError("须提交完备拆分子条（勿空提交依赖整包回退）")

    # 若只带 indices，先组装
    if children and all(
        isinstance(c, dict) and c.get("step_indices") and not c.get("procedure_slice")
        for c in children
    ):
        assembled = assemble_split_children(session, ip_id, children_spec=children)
        if not assembled.get("ok"):
            raise ValueError(
                "拆分提案不完备: " + "; ".join(assembled.get("errors") or [])
            )
        children = list(assembled.get("proposals") or [])

    children = _validate_split_children(session, row, list(children))

    now = _now_iso()
    src_claim = loads(row.claim_json, {})
    if not isinstance(src_claim, dict):
        src_claim = {}
    src_evidence = loads(row.evidence_json, {})
    src_concrete = loads(row.concrete_json, {})
    if not isinstance(src_evidence, dict):
        src_evidence = {}
    if not isinstance(src_concrete, dict):
        src_concrete = {}

    parent_snap = _ip_snapshot(row)
    split_count = len(children)
    new_ids: list[str] = []
    created: list[dict[str, Any]] = []
    for i, ch in enumerate(children):
        sum_i = str(ch["summary"])
        cat = str(ch.get("category") or "").strip() or infer_category(sum_i)
        proc_i = ch["procedure_slice"]
        child_after = list(ch.get("after") or [])
        lineage_ev = {
            "op": "split",
            "at": now,
            "actor": "user",
            "summary": f"由父条拆出第 {i + 1}/{split_count} 子条",
            "parent": parent_snap,
            "meta": {
                "split_index": i,
                "split_count": split_count,
                "source_id": ip_id,
            },
        }
        concrete_i = {
            **{
                k: v
                for k, v in src_concrete.items()
                if k not in ("after", "before", "lineage")
            },
            "split_from": ip_id,
            "split_index": i,
            "after": child_after,
            "before": [],
            "lineage": [lineage_ev],
        }
        claim_i = {
            **src_claim,
            "summary": sum_i,
            "kind": str(ch.get("kind") or src_claim.get("kind") or row.kind or ""),
            "split_from": ip_id,
            "split_index": i,
        }
        locus = make_locus_key(
            page_no=int(row.page_no or 0),
            category=cat,
            block_ids=list(src_concrete.get("block_ids") or []),
            slot=str(src_concrete.get("slot") or "") or f"split:{ip_id[:8]}:{i}",
        )
        child = CorpusImprovementPoint(
            fingerprint_hash=row.fingerprint_hash or "",
            source_job_id=row.source_job_id or "",
            source_session_id=row.source_session_id or "",
            locus_key=locus,
            page_no=int(row.page_no or 0),
            category=cat,
            authority=row.authority or "edition",
            status="accepted",
            claim_json=dumps(claim_i),
            evidence_json=dumps(src_evidence),
            procedure_json=dumps(proc_i),
            concrete_json=dumps(concrete_i),
            pattern_status="none",
            origin=str(row.origin or "") or "user_ask",
            summary=sum_i[:2000],
            kind=str(claim_i.get("kind") or "")[:64],
            needs_split=0,
            prep_status=PREP_NEEDS_TRIAGE,
            created_at=now,
            updated_at=now,
        )
        session.add(child)
        session.flush()
        if child.fingerprint_hash and child.authority != "pipeline":
            if (
                session.query(CorpusCorrection)
                .filter(CorpusCorrection.ip_id == child.id)
                .first()
                is None
            ):
                _write_correction_from_ip(session, child, child.fingerprint_hash)
        new_ids.append(child.id)

    # 回填兄弟 id，便于对照「同一父条拆出的其它子条」
    for cid in new_ids:
        crow = session.get(CorpusImprovementPoint, cid)
        if crow is None:
            continue
        cjson = loads(crow.concrete_json, {})
        if not isinstance(cjson, dict):
            continue
        lin = cjson.get("lineage")
        if not isinstance(lin, list) or not lin:
            continue
        head = lin[0]
        if not isinstance(head, dict):
            continue
        meta = dict(head.get("meta") or {})
        meta["sibling_ids"] = list(new_ids)
        head["meta"] = meta
        lin[0] = head
        cjson["lineage"] = lin
        crow.concrete_json = dumps(cjson)
        crow.updated_at = now
    session.flush()

    for cid in new_ids:
        crow = session.get(CorpusImprovementPoint, cid)
        if crow is not None:
            created.append(_ip_out(crow))

    purged = purge_improvement_point(session, ip_id)
    session.flush()
    return {
        "ok": True,
        "source_id": ip_id,
        "new_ids": new_ids,
        "created": created,
        "purged": purged.get("purged"),
    }


def _list_prep_queue(
    session: Session,
    *,
    prep_status: str,
    fingerprint_hash: str = "",
    ip_ids: list[str] | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    """按整理状态列出队列条目（含 steps/after 摘要），供 Agent 分批清完。"""
    fp = (fingerprint_hash or "").strip()
    prep = normalize_prep_status(prep_status)
    q = session.query(CorpusImprovementPoint).filter(
        CorpusImprovementPoint.status.in_(("accepted", "effective"))
    )
    if fp:
        q = q.filter(CorpusImprovementPoint.fingerprint_hash == fp)
    if ip_ids:
        q = q.filter(CorpusImprovementPoint.id.in_([str(x) for x in ip_ids]))
    else:
        q = q.filter(CorpusImprovementPoint.prep_status == prep)
    total_remaining = int(q.count())
    cap = max(1, min(int(limit or 2000), 5000))
    rows = (
        q.order_by(
            CorpusImprovementPoint.page_no.asc(),
            CorpusImprovementPoint.updated_at.desc(),
        )
        .limit(cap)
        .all()
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        proc = loads(r.procedure_json, {})
        if not isinstance(proc, dict):
            proc = {}
        steps = proc.get("steps") if isinstance(proc.get("steps"), list) else []
        step_brief = [
            {
                "i": i,
                "tool": str(s.get("tool") or "") if isinstance(s, dict) else "",
                "args_summary": str(
                    (s.get("args_summary") or s.get("summary") or "")
                    if isinstance(s, dict)
                    else ""
                )[:160],
            }
            for i, s in enumerate(steps)
        ]
        after_blocks = _parent_after_blocks(session, r)
        items.append(
            {
                "id": r.id,
                "page_no": int(r.page_no or 0),
                "summary": str(getattr(r, "summary", None) or "")[:500],
                "category": r.category,
                "prep_status": _row_prep(r),
                "steps": step_brief,
                "after_index": _after_index_list(after_blocks),
            }
        )
    return {
        "ok": True,
        "fingerprint_hash": fp,
        "prep_status": prep,
        "items": items,
        "count": len(items),
        "total_remaining": total_remaining,
        "has_more": total_remaining > len(items),
        "limit": cap,
    }


def list_for_triage(
    session: Session,
    *,
    fingerprint_hash: str = "",
    ip_ids: list[str] | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    """供 Cursor Agent 整理：待整理条目 + steps/after 摘要。"""
    return _list_prep_queue(
        session,
        prep_status=PREP_NEEDS_TRIAGE,
        fingerprint_hash=fingerprint_hash,
        ip_ids=ip_ids,
        limit=limit,
    )


def list_for_split(
    session: Session,
    *,
    fingerprint_hash: str = "",
    ip_ids: list[str] | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    """供 Cursor Agent 全部拆分：待拆分条目 + steps/after 摘要。"""
    return _list_prep_queue(
        session,
        prep_status=PREP_NEEDS_SPLIT,
        fingerprint_hash=fingerprint_hash,
        ip_ids=ip_ids,
        limit=limit,
    )


def conflict_hints(
    session: Session,
    *,
    fingerprint_hash: str = "",
) -> dict[str, Any]:
    """规则对立 hint（不写库），供 Agent 参考。"""
    fp = (fingerprint_hash or "").strip()
    if not fp:
        raise ValueError("fingerprint_hash required")
    rows = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.fingerprint_hash == fp)
        .filter(CorpusImprovementPoint.status.in_(("accepted", "effective")))
        .all()
    )
    open_rows = [r for r in rows if _row_prep(r) in _PREP_CONFLICT_SCAN]
    hints: list[dict[str, Any]] = []
    for i, left in enumerate(open_rows):
        for right in open_rows[i + 1 :]:
            if int(left.page_no or 0) != int(right.page_no or 0):
                continue
            sa = str(getattr(left, "summary", None) or "")
            sb = str(getattr(right, "summary", None) or "")
            reason = _opposing_verbs(sa, sb)
            if not (reason and _summaries_look_similar(sa, sb)):
                continue
            hints.append(
                {
                    "a_id": left.id,
                    "b_id": right.id,
                    "page_no": int(left.page_no or 0),
                    "reason": reason,
                    "summary_a": sa[:200],
                    "summary_b": sb[:200],
                }
            )
    return {"ok": True, "fingerprint_hash": fp, "hints": hints, "count": len(hints)}


def triage_apply(
    session: Session,
    *,
    fingerprint_hash: str = "",
    ready_ids: list[str] | None = None,
    split_ids: list[str] | None = None,
    aggregate_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Cursor Agent 整理结果落库（无模型）。同条优先待拆分。"""
    fp = (fingerprint_hash or "").strip()
    ready_set = {str(x).strip() for x in (ready_ids or []) if str(x).strip()}
    split_set = {str(x).strip() for x in (split_ids or []) if str(x).strip()}
    groups = list(aggregate_groups or [])

    # 收集本次允许改写的 id（同书待整理，或结果里出现的 id）
    allowed: set[str] = set()
    q = session.query(CorpusImprovementPoint)
    if fp:
        q = q.filter(CorpusImprovementPoint.fingerprint_hash == fp)
    for r in q.all():
        if _row_prep(r) == PREP_NEEDS_TRIAGE or r.id in ready_set | split_set:
            allowed.add(r.id)
    for g in groups:
        if isinstance(g, dict):
            for mid in g.get("member_ids") or []:
                allowed.add(str(mid).strip())

    unknown = (ready_set | split_set) - allowed
    for g in groups:
        if not isinstance(g, dict):
            continue
        for mid in g.get("member_ids") or []:
            if str(mid).strip() not in allowed and str(mid).strip():
                # 允许非 triage 若在同书
                row = session.get(CorpusImprovementPoint, str(mid).strip())
                if row is None or (fp and row.fingerprint_hash != fp):
                    unknown.add(str(mid).strip())
                else:
                    allowed.add(str(mid).strip())
    if unknown:
        raise ValueError(f"非法或不在本书范围的 id: {sorted(unknown)[:20]}")

    # 优先级：split > aggregate > ready
    agg_members: set[str] = set()
    now = _now_iso()
    applied = {"ready": [], "split": [], "aggregate": [], "skipped_triage": []}

    for sid in split_set:
        row = session.get(CorpusImprovementPoint, sid)
        if row is None:
            continue
        # 硬复核：明显单步且摘要切不出多意图 → 留待整理
        proc = loads(row.procedure_json, {})
        steps = (proc.get("steps") or []) if isinstance(proc, dict) else []
        sm = str(getattr(row, "summary", None) or "")
        if len(steps) < 2 and not _summary_is_multi_topic(sm):
            applied["skipped_triage"].append(sid)
            continue
        _set_prep(row, PREP_NEEDS_SPLIT, now=now)
        applied["split"].append(sid)

    for g in groups:
        if not isinstance(g, dict):
            continue
        members = [str(x).strip() for x in (g.get("member_ids") or []) if str(x).strip()]
        members = [m for m in members if m not in split_set]
        if len(members) < 2:
            continue
        gid = str(uuid.uuid4())
        reason = str(g.get("reason") or "")[:400]
        for mid in members:
            row = session.get(CorpusImprovementPoint, mid)
            if row is None:
                continue
            _set_prep(row, PREP_NEEDS_AGGREGATE, now=now)
            concrete = loads(row.concrete_json, {})
            if not isinstance(concrete, dict):
                concrete = {}
            concrete["aggregate_group_id"] = gid
            concrete["aggregate_reason"] = reason
            row.concrete_json = dumps(concrete)
            agg_members.add(mid)
            applied["aggregate"].append(mid)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                _link_conflicts(session, a, b)

    for rid in ready_set:
        if rid in split_set or rid in agg_members:
            continue
        row = session.get(CorpusImprovementPoint, rid)
        if row is None:
            continue
        _set_prep(row, PREP_READY, now=now)
        applied["ready"].append(rid)

    session.flush()
    return {"ok": True, "fingerprint_hash": fp, "applied": applied}


def list_conflict_pairs(
    session: Session,
    *,
    fingerprint_hash: str = "",
    detect: bool = True,
) -> dict[str, Any]:
    """列出本书矛盾对；默认先跑轻量检测再汇总。"""
    fp = (fingerprint_hash or "").strip()
    if not fp:
        raise ValueError("fingerprint_hash required")
    # detect=True 时仅返回规则 hint，不写库（写库走 triage_apply）
    newly = (
        conflict_hints(session, fingerprint_hash=fp).get("hints") or []
        if detect
        else []
    )
    rows = (
        session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.fingerprint_hash == fp)
        .filter(
            CorpusImprovementPoint.prep_status.in_(
                (PREP_NEEDS_AGGREGATE, PREP_NEEDS_CONFLICT)
            )
        )
        .all()
    )
    by_id = {r.id: r for r in rows}
    links = (
        session.query(CorpusImprovementLink)
        .filter(CorpusImprovementLink.rel == "conflicts")
        .all()
    )
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        a, b = str(link.from_id or ""), str(link.to_id or "")
        if a not in by_id or b not in by_id:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        ra, rb = by_id[a], by_id[b]
        sa = str(getattr(ra, "summary", None) or "")
        sb = str(getattr(rb, "summary", None) or "")
        reason = _opposing_verbs(sa, sb) or "conflicts 链接"
        pairs.append(
            {
                "a_id": a,
                "b_id": b,
                "page_no": int(ra.page_no or rb.page_no or 0),
                "reason": reason,
                "summary_a": sa[:400],
                "summary_b": sb[:400],
                "prep_status_a": _row_prep(ra),
                "prep_status_b": _row_prep(rb),
            }
        )
    # 无链接但仍标记矛盾的：按同页两两补出（仅展示）
    by_page: dict[int, list[CorpusImprovementPoint]] = {}
    for r in rows:
        by_page.setdefault(int(r.page_no or 0), []).append(r)
    for page, group in by_page.items():
        for i, left in enumerate(group):
            for right in group[i + 1 :]:
                key = tuple(sorted((left.id, right.id)))
                if key in seen:
                    continue
                sa = str(getattr(left, "summary", None) or "")
                sb = str(getattr(right, "summary", None) or "")
                reason = _opposing_verbs(sa, sb)
                if not reason and not _summaries_look_similar(sa, sb):
                    continue
                seen.add(key)
                pairs.append(
                    {
                        "a_id": left.id,
                        "b_id": right.id,
                        "page_no": page,
                        "reason": reason or "同页待裁决",
                        "summary_a": sa[:400],
                        "summary_b": sb[:400],
                        "prep_status_a": _row_prep(left),
                        "prep_status_b": _row_prep(right),
                    }
                )
    return {
        "ok": True,
        "fingerprint_hash": fp,
        "pairs": pairs,
        "newly_marked": newly,
        "count": len(pairs),
    }


def resolve_prep_conflicts(
    session: Session,
    *,
    keep_ids: list[str] | None = None,
    delete_ids: list[str] | None = None,
) -> dict[str, Any]:
    """整理态矛盾裁决：删之前把对立方摘要写入保留条历程，再 purge 删除方。"""
    keep = [str(x).strip() for x in (keep_ids or []) if str(x).strip()]
    delete = [str(x).strip() for x in (delete_ids or []) if str(x).strip()]
    if not keep and not delete:
        raise ValueError("keep_ids 或 delete_ids 至少一项非空")
    now = _now_iso()
    deleted: list[str] = []
    skipped: list[str] = []

    # 删之前先复印被删方摘要（否则 purge 后无正文可追）
    delete_snaps: list[dict[str, Any]] = []
    for did in delete:
        drow = session.get(CorpusImprovementPoint, did)
        if drow is None:
            skipped.append(did)
            continue
        delete_snaps.append(_ip_snapshot(drow))

    for did in delete:
        if session.get(CorpusImprovementPoint, did) is None:
            continue
        purge_improvement_point(session, did)
        deleted.append(did)

    # 清掉涉及已删方的 conflicts 链接
    if deleted:
        for link in (
            session.query(CorpusImprovementLink)
            .filter(CorpusImprovementLink.rel == "conflicts")
            .all()
        ):
            if link.from_id in deleted or link.to_id in deleted:
                session.delete(link)

    kept_out: list[dict[str, Any]] = []
    for kid in keep:
        row = session.get(CorpusImprovementPoint, kid)
        if row is None:
            skipped.append(kid)
            continue
        concrete = loads(row.concrete_json, {})
        if not isinstance(concrete, dict):
            concrete = {}
        gid = str(concrete.get("aggregate_group_id") or "").strip()
        reason = str(concrete.get("aggregate_reason") or "").strip()
        n_del = len(delete_snaps)
        lineage_ev = {
            "op": "aggregate_keep",
            "at": now,
            "actor": "user",
            "summary": (
                f"聚合裁决：保留本条，删除 {n_del} 条对立"
                + (f"（{reason}）" if reason else "")
            ),
            "peers": delete_snaps,
            "meta": {
                "aggregate_group_id": gid,
                "aggregate_reason": reason[:400],
                "kept_ids": list(keep),
                "deleted_ids": list(deleted),
            },
        }
        concrete = _append_lineage(concrete, lineage_ev)
        row.concrete_json = dumps(concrete)
        row.updated_at = now
        still = (
            session.query(CorpusImprovementLink)
            .filter(
                CorpusImprovementLink.rel == "conflicts",
                (
                    (CorpusImprovementLink.from_id == kid)
                    | (CorpusImprovementLink.to_id == kid)
                ),
            )
            .first()
        )
        if still is None:
            _set_prep(row, PREP_READY, now=now)
        kept_out.append(_ip_out(row))

    session.flush()
    return {
        "ok": True,
        "deleted_ids": deleted,
        "kept": kept_out,
        "skipped_ids": skipped,
    }


def _job_fingerprint(meta: dict[str, Any]) -> str:
    bf = meta.get("book_fingerprint")
    if isinstance(bf, dict):
        h = str(bf.get("hash") or "").strip()
        if h:
            return h
    return str(meta.get("fingerprint_hash") or "").strip()


def list_ip_books(session: Session) -> dict[str, Any]:
    """侧栏用：按改进点真实 fingerprint 分组，避免「只列当前导入任务」漏掉孤儿书指纹。"""
    rows = (
        session.query(
            CorpusImprovementPoint.fingerprint_hash,
            CorpusImprovementPoint.source_job_id,
        )
        .filter(CorpusImprovementPoint.status.in_(("accepted", "effective")))
        .all()
    )
    by_fp: dict[str, dict[str, Any]] = {}
    for fp_raw, job_raw in rows:
        fp = str(fp_raw or "").strip() or "(empty)"
        job = str(job_raw or "").strip()
        slot = by_fp.setdefault(
            fp, {"fingerprint_hash": "" if fp == "(empty)" else fp, "count": 0, "job_ids": {}}
        )
        slot["count"] += 1
        if job:
            slot["job_ids"][job] = int(slot["job_ids"].get(job, 0)) + 1

    # 现有导入任务：指纹 → 标签
    fp_labels: dict[str, str] = {}
    job_labels: dict[str, str] = {}
    job_fps: dict[str, str] = {}
    for job in session.query(CorpusImportJob).all():
        meta = loads(job.meta_json, {})
        if not isinstance(meta, dict):
            meta = {}
        jfp = _job_fingerprint(meta)
        label = str(job.label or meta.get("label") or meta.get("filename") or job.id)
        job_labels[job.id] = label
        if jfp:
            job_fps[job.id] = jfp
            fp_labels.setdefault(jfp, label)

    books: list[dict[str, Any]] = []
    for fp, slot in by_fp.items():
        real_fp = "" if fp == "(empty)" else fp
        label = fp_labels.get(real_fp, "")
        top_job = ""
        if slot["job_ids"]:
            top_job = max(slot["job_ids"].items(), key=lambda x: x[1])[0]
            if not label:
                label = job_labels.get(top_job, "")
        if not label:
            label = f"未关联任务 · {real_fp[:10] or '无指纹'}…"
        books.append(
            {
                "fingerprint_hash": real_fp,
                "label": label,
                "count": int(slot["count"]),
                "source_job_id": top_job,
                "job_fingerprint_mismatch": bool(
                    top_job
                    and job_fps.get(top_job)
                    and real_fp
                    and job_fps.get(top_job) != real_fp
                ),
            }
        )
    books.sort(key=lambda b: (-int(b["count"]), str(b["label"])))
    total = sum(int(b["count"]) for b in books)
    return {"ok": True, "total": total, "books": books}


def list_improvement_points(
    session: Session,
    *,
    fingerprint_hash: str = "",
    job_id: str = "",
    status: str = "",
    prep_status: str = "",
    page_no: int | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    q = session.query(CorpusImprovementPoint)
    fp = (fingerprint_hash or "").strip()
    jid = (job_id or "").strip()
    # 有书指纹时只按指纹筛（侧栏按真实指纹列书）；无指纹才退回任务 id
    if fp:
        q = q.filter(CorpusImprovementPoint.fingerprint_hash == fp)
    elif jid:
        q = q.filter(CorpusImprovementPoint.source_job_id == jid)
    if status:
        q = q.filter(CorpusImprovementPoint.status == status)
    if prep_status:
        q = q.filter(CorpusImprovementPoint.prep_status == prep_status.strip())
    if page_no is not None:
        q = q.filter(CorpusImprovementPoint.page_no == int(page_no))
    rows = (
        q.order_by(
            CorpusImprovementPoint.page_no.asc(),
            CorpusImprovementPoint.updated_at.desc(),
        )
        .limit(max(1, min(limit, 2000)))
        .all()
    )
    items = [_ip_out(r) for r in rows]
    # Cluster view helpers
    by_locus: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_locus.setdefault(it["locus_key"], []).append(it)
    by_dialogue: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_dialogue.setdefault(str(it.get("dialogue_key") or ""), []).append(it)
    # 版本链：同定位键下的旧版收在当前版下面，列表默认不再平铺
    for locus, group in by_locus.items():
        older = [g for g in group if g["status"] in ("superseded", "rejected")]
        for it in group:
            it["version_count"] = len(group)
            it["older_count"] = len(older) if it["status"] not in (
                "superseded",
                "rejected",
            ) else 0
            it["older_ids"] = (
                [str(g["id"]) for g in older]
                if it["status"] not in ("superseded", "rejected")
                else []
            )
    for key, group in by_dialogue.items():
        for it in group:
            it["dialogue_size"] = len(group)
    conflicts = [it for it in items if it["status"] == "conflict"]
    accepted = [it for it in items if it["status"] == "accepted"]
    by_status: dict[str, int] = {}
    for it in items:
        st = str(it.get("status") or "unknown")
        by_status[st] = by_status.get(st, 0) + 1
    pending = (
        by_status.get("observed", 0)
        + by_status.get("clustered", 0)
        + by_status.get("candidate", 0)
    )
    by_prep: dict[str, int] = {
        PREP_NEEDS_TRIAGE: 0,
        PREP_NEEDS_SPLIT: 0,
        PREP_NEEDS_AGGREGATE: 0,
        PREP_READY: 0,
    }
    for it in items:
        ps = normalize_prep_status(str(it.get("prep_status") or PREP_NEEDS_TRIAGE))
        by_prep[ps] = by_prep.get(ps, 0) + 1
    return {
        "items": items,
        "by_locus": by_locus,
        "by_dialogue": by_dialogue,
        "conflicts": conflicts,
        "accepted": accepted,
        "counts": {
            "total": len(items),
            "accepted": len(accepted),
            "conflict": len(conflicts),
            "superseded": by_status.get("superseded", 0),
            "rejected": by_status.get("rejected", 0),
            "pending": pending,
            "locus": len(by_locus),
            "dialogue": len(by_dialogue),
            "merged": sum(1 for it in items if it.get("merged_into_id")),
            "by_status": by_status,
            "by_prep_status": by_prep,
        },
    }


def bind_book_edition(
    session: Session,
    fingerprint_hash: str,
    edition_id: str,
    *,
    source_job_id: str = "",
    note: str = "",
) -> dict[str, Any]:
    fp = (fingerprint_hash or "").strip()
    eid = (edition_id or "").strip()
    if not fp or not eid:
        raise ValueError("fingerprint_hash and edition_id required")
    now = _now_iso()
    row = session.get(CorpusBookEditionBind, fp)
    if row is None:
        row = CorpusBookEditionBind(
            fingerprint_hash=fp,
            edition_id=eid,
            source_job_id=source_job_id or "",
            note=note or "",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.edition_id = eid
        row.source_job_id = source_job_id or row.source_job_id
        row.note = note or row.note
        row.updated_at = now
    session.flush()
    return {
        "fingerprint_hash": fp,
        "edition_id": eid,
        "source_job_id": row.source_job_id,
        "updated_at": row.updated_at,
    }


def get_book_edition_bind(session: Session, fingerprint_hash: str) -> dict[str, Any] | None:
    row = session.get(CorpusBookEditionBind, (fingerprint_hash or "").strip())
    if row is None:
        return None
    return {
        "fingerprint_hash": row.fingerprint_hash,
        "edition_id": row.edition_id,
        "source_job_id": row.source_job_id,
        "note": row.note,
        "updated_at": row.updated_at,
    }


def procedures_for_fingerprint(
    session: Session, fingerprint_hash: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """Summaries for Copilot autofix injection after replay."""
    rows = (
        session.query(CorpusImprovementPoint)
        .filter(
            CorpusImprovementPoint.fingerprint_hash == fingerprint_hash,
            CorpusImprovementPoint.status == "accepted",
        )
        .order_by(CorpusImprovementPoint.page_no.asc())
        .limit(limit)
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        proc = loads(r.procedure_json, {})
        claim = loads(r.claim_json, {})
        out.append(
            {
                "ip_id": r.id,
                "page_no": r.page_no,
                "category": r.category,
                "summary": claim.get("summary"),
                "procedure": proc,
            }
        )
    return out

#!/usr/bin/env python3
"""按书指纹合并改进点 AI 会话（ip_triage / ip_split → ip_book）。

一书指纹只保留一条对话。组内若有 running → 跳过。
幸存者 = updated_at 最新（保留 agent_id 便于续聊）。
消息重排：**整理在前、拆分在后**（按原会话 kind，同 kind 内按 created_at/seq）。

用法（在 buddy-copilot 目录，服务空闲时）：

  python3 scripts/merge-ip-sessions-by-fingerprint.py
  python3 scripts/merge-ip-sessions-by-fingerprint.py --dry-run
  python3 scripts/merge-ip-sessions-by-fingerprint.py --fingerprint 038e8a19…
  python3 scripts/merge-ip-sessions-by-fingerprint.py --reorder-only  # 已合并会话按序重排
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402

IP_KINDS = ("ip_book", "ip_triage", "ip_split")
RUNNING_STATUSES = frozenset({"planning", "executing", "running"})


def _fp(payload_text: str) -> str:
    try:
        pl = json.loads(payload_text or "{}")
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(pl, dict):
        return ""
    return str(pl.get("fingerprint_hash") or "").strip()


def _kind_rank(kind: str) -> int:
    """显示顺序：整理 → 其它/已合并 → 拆分。"""
    k = (kind or "").strip()
    if k == "ip_triage":
        return 0
    if k == "ip_split":
        return 2
    return 1


def _section_label(rank: int) -> str:
    if rank == 0:
        return "—— ① 整理 ——"
    if rank == 2:
        return "—— ② 拆分 ——"
    return "—— 本书改进点 ——"


def _rewrite_messages(
    conn: sqlite3.Connection,
    survivor_id: str,
    blocks: list[tuple[int, list[tuple[str, str, float]]]],
    *,
    note: str,
) -> None:
    """blocks: [(kind_rank, [(role, content, created_at), ...]), ...] 已按整理→拆分排序。"""
    conn.execute("DELETE FROM messages WHERE session_id=?", (survivor_id,))
    now = time.time()
    seq = 0
    seq += 1
    conn.execute(
        "INSERT INTO messages (session_id, seq, role, content, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            survivor_id,
            seq,
            "system",
            json.dumps({"text": note}, ensure_ascii=False),
            now,
        ),
    )
    for rank, msgs in blocks:
        if not msgs:
            continue
        seq += 1
        conn.execute(
            "INSERT INTO messages (session_id, seq, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                survivor_id,
                seq,
                "system",
                json.dumps({"text": _section_label(rank)}, ensure_ascii=False),
                now,
            ),
        )
        for role, content, created_at in msgs:
            # 跳过旧合并标记，避免重复
            try:
                blob = json.loads(content or "{}")
            except Exception:  # noqa: BLE001
                blob = {}
            text = str((blob or {}).get("text") or "")
            if "已合并同书" in text or text.startswith("—— ①") or text.startswith("—— ②"):
                continue
            if "已与导入任务绑定（空对话）" in text:
                continue
            seq += 1
            conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (survivor_id, seq, role, content, created_at or now),
            )


def _collect_session_msgs(
    conn: sqlite3.Connection, sid: str
) -> list[tuple[str, str, float]]:
    rows = conn.execute(
        "SELECT role, content, created_at FROM messages"
        " WHERE session_id=? ORDER BY seq ASC",
        (sid,),
    ).fetchall()
    out: list[tuple[str, str, float]] = []
    for msg in rows:
        out.append(
            (str(msg["role"]), str(msg["content"]), float(msg["created_at"] or 0))
        )
    return out


def reorder_existing(conn: sqlite3.Connection, fp_filter: str, dry_run: bool) -> int:
    """对已是 ip_book 的单会话：若能从消息分段识别则重排；否则仅加章节头。

    当前库在合并后已无独立 triage 会话时：把「拆分」正文放到 ②，并注明 ① 原文缺失。
    """
    rows = conn.execute(
        "SELECT id, kind, status, payload, created_at, updated_at FROM sessions"
        " WHERE kind='ip_book' ORDER BY updated_at DESC"
    ).fetchall()
    n = 0
    for r in rows:
        fp = _fp(r["payload"])
        if not fp:
            continue
        if fp_filter and not fp.lower().startswith(fp_filter.lower()):
            continue
        msgs = _collect_session_msgs(conn, r["id"])
        # 粗分：强拆分信号 → 拆分块；强整理信号 → 整理块；其余跟邻近块
        triage: list[tuple[str, str, float]] = []
        split: list[tuple[str, str, float]] = []
        split_kw = (
            "拆分",
            "split_all",
            "needs_split",
            "list_for_split",
            "split_apply",
            "split_propose",
            "完备路径",
            "ip_split",
        )
        triage_kw = (
            "待整理",
            "needs_triage",
            "list_for_triage",
            "triage_apply",
            "改进点整理",
            "ip_triage",
            "remaining_needs_triage",
        )
        last = "split"  # 默认偏拆分（一书一对话后常见）
        for role, content, created_at in msgs:
            try:
                blob = json.loads(content or "{}")
            except Exception:  # noqa: BLE001
                blob = {}
            text = str((blob or {}).get("text") or "")
            packed = content or ""
            if (
                "已合并同书" in text
                or "已按一书一对话合并" in text
                or text.startswith("——")
                or "整理对话原文不可用" in text
                or "已与导入任务绑定（空对话）" in text
            ):
                continue
            is_split = any(k in packed for k in split_kw)
            is_triage = any(k in packed for k in triage_kw)
            if is_split and not is_triage:
                last = "split"
                split.append((role, content, created_at))
            elif is_triage and not is_split:
                last = "triage"
                triage.append((role, content, created_at))
            elif last == "triage":
                triage.append((role, content, created_at))
            else:
                split.append((role, content, created_at))

        # 只有拆分、没有整理原文：① 占位，全文进 ②
        if not triage and split:
            print(
                f"REORDER {fp[:12]}… sid={r['id'][:8]} "
                f"triage_msgs=0(split-only) split_msgs={len(split)}"
            )
        else:
            print(
                f"REORDER {fp[:12]}… sid={r['id'][:8]} "
                f"triage_msgs={len(triage)} split_msgs={len(split)}"
            )
        if dry_run:
            n += 1
            continue
        note = "已按一书一对话合并显示。顺序：①整理 → ②拆分。"
        if not triage:
            triage = [
                (
                    "system",
                    json.dumps(
                        {
                            "text": (
                                "整理对话原文不可用（旧 ip_triage 会话已在合并前删除）。"
                                "之后点「整理」会在本会话继续产生新气泡。"
                            )
                        },
                        ensure_ascii=False,
                    ),
                    time.time(),
                )
            ]
        blocks: list[tuple[int, list[tuple[str, str, float]]]] = [
            (0, triage),
            (2, split),
        ]
        _rewrite_messages(conn, r["id"], blocks, note=note)
        conn.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?",
            (time.time(), r["id"]),
        )
        conn.commit()
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Merge IP Copilot sessions by fingerprint")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写库")
    ap.add_argument(
        "--fingerprint",
        default="",
        help="只处理指定 fingerprint_hash（可前缀匹配）",
    )
    ap.add_argument(
        "--db",
        default="",
        help="覆盖 COPILOT_DB_PATH（默认读 settings）",
    )
    ap.add_argument(
        "--reorder-only",
        action="store_true",
        help="不合并多会话，只把已有 ip_book 消息排成整理在前、拆分在后",
    )
    args = ap.parse_args()
    settings = get_settings()
    db_path = Path(args.db).expanduser() if args.db else settings.db_path
    if not db_path.is_file():
        print(f"ERROR: db not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    fp_filter = (args.fingerprint or "").strip()

    print(f"db={db_path}")
    if args.dry_run:
        print("mode=dry-run")

    if args.reorder_only:
        n = reorder_existing(conn, fp_filter, args.dry_run)
        print(f"done reorder={n}")
        conn.close()
        return 0

    rows = conn.execute(
        "SELECT id, kind, status, payload, created_at, updated_at"
        " FROM sessions WHERE kind IN (?,?,?) ORDER BY updated_at DESC",
        IP_KINDS,
    ).fetchall()

    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    no_fp = 0
    for r in rows:
        fp = _fp(r["payload"])
        if not fp:
            no_fp += 1
            continue
        if fp_filter and not fp.lower().startswith(fp_filter.lower()):
            continue
        groups[fp].append(r)

    print(f"ip sessions={len(rows)} groups={len(groups)} no_fingerprint={no_fp}")

    merged_groups = 0
    skipped = 0
    deleted = 0

    for fp, members in sorted(groups.items(), key=lambda x: x[0]):
        if len(members) < 2 and all(m["kind"] == "ip_book" for m in members):
            continue
        if len(members) < 2:
            m = members[0]
            if m["kind"] != "ip_book":
                print(f"UPGRADE {fp[:12]}… sid={m['id'][:8]} {m['kind']}→ip_book")
                if not args.dry_run:
                    conn.execute(
                        "UPDATE sessions SET kind=?, updated_at=? WHERE id=?",
                        ("ip_book", time.time(), m["id"]),
                    )
                    conn.commit()
                merged_groups += 1
            continue

        if any(str(m["status"] or "") in RUNNING_STATUSES for m in members):
            ids = ", ".join(f"{m['id'][:8]}({m['status']})" for m in members)
            print(f"SKIP running {fp[:12]}… [{ids}]")
            skipped += 1
            continue

        # 幸存者 = updated_at 最新（保留 agent_id）
        by_updated = sorted(
            members, key=lambda m: float(m["updated_at"] or 0), reverse=True
        )
        survivor = by_updated[0]
        victims = [m for m in members if m["id"] != survivor["id"]]

        # 按 kind 收集：整理会话整段 → 拆分会话整段（同 kind 按会话创建时间）
        by_rank: dict[int, list[tuple[str, str, float]]] = defaultdict(list)
        for m in sorted(
            members,
            key=lambda x: (_kind_rank(str(x["kind"])), float(x["created_at"] or 0)),
        ):
            rank = _kind_rank(str(m["kind"]))
            # 已是 ip_book 的幸存者：按内容粗分困难，整段放 middle；合并时旧 kind 为准
            if m["kind"] == "ip_book" and m["id"] == survivor["id"]:
                # survivor 若本是 split/triage 升级前已是 book，用 payload.intent 提示
                try:
                    pl = json.loads(m["payload"] or "{}")
                except Exception:  # noqa: BLE001
                    pl = {}
                intent = str((pl or {}).get("intent") or "").lower()
                if intent in ("split_all", "split_one"):
                    rank = 2
                elif intent in ("triage", "aggregate"):
                    rank = 0
                elif str(m["kind"]) == "ip_book":
                    # 看原 kind 已丢失：时间上若与 split 会话同时代，放到拆分前的「书」段
                    rank = 1
            by_rank[rank].extend(_collect_session_msgs(conn, m["id"]))

        print(
            f"MERGE {fp[:12]}… survivor={survivor['id'][:8]}({survivor['kind']}) "
            f"+{len(victims)} victims ranks={{{', '.join(f'{k}:{len(v)}' for k,v in sorted(by_rank.items()))}}}"
        )

        if args.dry_run:
            merged_groups += 1
            deleted += len(victims)
            continue

        blocks = [(rank, by_rank[rank]) for rank in sorted(by_rank.keys()) if by_rank[rank]]
        _rewrite_messages(
            conn,
            survivor["id"],
            blocks,
            note="已合并同书历史整理/拆分对话。显示顺序：①整理 → ②拆分。",
        )

        try:
            pl = json.loads(survivor["payload"] or "{}")
        except Exception:  # noqa: BLE001
            pl = {}
        if not isinstance(pl, dict):
            pl = {}
        pl["fingerprint_hash"] = fp
        pl.setdefault("intent", "triage")
        now = time.time()
        conn.execute(
            "UPDATE sessions SET kind=?, payload=?, updated_at=? WHERE id=?",
            (
                "ip_book",
                json.dumps(pl, ensure_ascii=False),
                now,
                survivor["id"],
            ),
        )

        for v in victims:
            conn.execute("DELETE FROM messages WHERE session_id=?", (v["id"],))
            conn.execute("DELETE FROM approvals WHERE session_id=?", (v["id"],))
            conn.execute("DELETE FROM audit_log WHERE session_id=?", (v["id"],))
            conn.execute("DELETE FROM sessions WHERE id=?", (v["id"],))
            deleted += 1

        conn.commit()
        merged_groups += 1

    print(
        f"done merged_or_upgraded={merged_groups} deleted={deleted} skipped_running={skipped}"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

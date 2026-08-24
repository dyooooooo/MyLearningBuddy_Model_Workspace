#!/usr/bin/env python3
"""Match Copilot user dialogues to improvement points — page-strict, full text.

Matching rules (Learning Buddy):
  1. IP.page_no must equal focus page_no
  2. 「打开方案 / 按建议改 / 继续…」是确认/操控，不是问题口令
  3. 只认聚焦模板后的正文；纯聚焦空发不算用户输入
  4. 用户输入 = 完整正文（不截断）；同轮全部用户话术写入 user_requests
  5. 优先按 block_id 对齐对话；否则摘要贴近；再否则该页最早实质对话
  6. 该页无实质对话时清空错误口令（禁止跨页粘贴）

  python3 tools/backfill-ip-user-from-session.py --session e76d566110884eae --force --dry-run
  python3 tools/backfill-ip-user-from-session.py --session e76d566110884eae --force
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PAGE_RE = re.compile(r"page_no\s*=\s*(\d+)", re.I)
_BLOCK_RE = re.compile(r"block_id\s*=\s*([^\s]+)", re.I)
_SKIP_ASK = re.compile(
    r"^(按建议改|继续|不修|下一批|好的|ok|OK|是|否|同意|取消|"
    r"打开方案|记入当前|生成新排版|下一批|"
    r"不修[，,].*|先这样.*)$",
    re.I,
)
_CONTROL_SUBSTR = (
    "打开方案",
    "记入当前",
    "待审队列",
    "生成新排版方案",
    "__ui:",
)


def strip_focus(text: str) -> str:
    t = (text or "").strip()
    if "【聚焦目标】" not in t and "page_no=" not in t:
        return t
    body = []
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
    return "\n".join(body).strip()


def extract_block_id(text: str) -> str:
    m = _BLOCK_RE.search(text or "")
    if not m:
        return ""
    bid = m.group(1).strip()
    if bid in ("", "None", "null"):
        return ""
    return bid


def is_control(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if _SKIP_ASK.match(t):
        return True
    if any(x in t for x in _CONTROL_SUBSTR) and len(t) < 80:
        return True
    if t.startswith("打开方案"):
        return True
    return False


def is_good_ask(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2:
        return False
    if is_control(t):
        return False
    # Must be free text, not the focus shell itself
    if "【聚焦目标】" in t or t.startswith("page_no="):
        return False
    return True


def load_user_turns(db: Path, session_id: str) -> list[dict]:
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT seq, content, created_at FROM messages "
        "WHERE session_id=? AND role='user' ORDER BY seq",
        (session_id,),
    ).fetchall()
    con.close()
    turns = []
    for seq, content, created_at in rows:
        try:
            obj = json.loads(content or "{}")
            text = str(obj.get("text") or content or "").strip()
        except json.JSONDecodeError:
            text = str(content or "").strip()
        if not text:
            continue
        m = _PAGE_RE.search(text)
        page = int(m.group(1)) if m else 0
        ask = strip_focus(text)
        turns.append(
            {
                "seq": int(seq),
                "text": text,  # full raw message, never truncated
                "page_no": page,
                "block_id": extract_block_id(text) if page > 0 else "",
                "ask": ask,
                "created_at": float(created_at or 0),
            }
        )
    return turns


def dialogues_by_page(turns: list[dict]) -> dict[int, list[dict]]:
    """One dialogue = (optional pre-focus user turns) + focus(page P) + follow-ups.

    Stores FULL raw user messages (Studio focus shell + free text). Never strips
    for storage — stripping is only used to decide whether a turn is substantive.
    """
    by_page: dict[int, list[dict]] = {}
    current_page = 0
    buf: list[dict] = []
    pre: list[dict] = []  # user turns before the next focus
    start_seq = 0
    start_ts = 0.0
    start_block = ""

    def flush() -> None:
        nonlocal buf, start_seq, start_ts, current_page, start_block
        if current_page > 0 and buf:
            raw_chrono: list[str] = [b["text"] for b in buf]
            asks_chrono: list[str] = []
            for b in buf:
                a = strip_focus(b["text"])
                if a:
                    asks_chrono.append(a)
            good = [a for a in asks_chrono if is_good_ask(a)]
            primary_free = good[0] if good else ""
            # Primary = first focus message that carries the first good ask,
            # else first focus message in the dialogue.
            primary_raw = ""
            for b in buf:
                if b.get("page_no", 0) > 0 and strip_focus(b["text"]) == primary_free:
                    primary_raw = b["text"]
                    break
            if not primary_raw:
                for b in buf:
                    if b.get("page_no", 0) > 0:
                        primary_raw = b["text"]
                        break
            if not primary_raw and buf:
                primary_raw = buf[0]["text"]
            by_page.setdefault(current_page, []).append(
                {
                    "page_no": current_page,
                    "seq": start_seq,
                    "created_at": start_ts,
                    "block_id": start_block,
                    # user_input = FULL raw (focus format + body)
                    "user_input": primary_raw,
                    "user_input_raw": primary_raw,
                    "user_input_free": primary_free,
                    # all raw user messages in this dialogue (pre + focus + follow-ups)
                    "user_requests": list(raw_chrono),
                    "user_requests_raw": list(raw_chrono),
                    "asks_good": list(good),
                    "has_problem": bool(good) or bool(primary_raw),
                }
            )
        buf = []

    for t in turns:
        if t["page_no"] > 0:
            flush()
            current_page = t["page_no"]
            start_seq = t["seq"]
            start_ts = float(t.get("created_at") or 0)
            start_block = str(t.get("block_id") or "")
            # Attach pre-focus user turns (no page_no) to this dialogue
            buf = list(pre) + [t]
            pre = []
        elif current_page > 0:
            buf.append(t)
        else:
            pre.append(t)
    flush()
    return by_page


def _iso_to_ts(iso: str) -> float:
    s = (iso or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def score_dialogue(d: dict, summary: str, block_id: str = "") -> int:
    if not d.get("has_problem"):
        return -100
    score = 1
    bid = str(block_id or "").strip()
    if bid and bid == str(d.get("block_id") or "").strip():
        score += 50
    blob = " ".join(d.get("asks_good") or [])
    sum_l = summary or ""
    for tok in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", blob):
        if tok in sum_l:
            score += 2
    for key in ("封面", "公式", "页眉", "图", "例", "练习", "目录", "开本", "版权", "表格"):
        if key in blob and key in sum_l:
            score += 5
    return score


def pick_dialogue(
    page_dialogs: list[dict],
    *,
    summary: str,
    block_id: str = "",
    before_ts: float = 0.0,
) -> dict | None:
    pool = list(page_dialogs or [])
    if before_ts > 0:
        earlier = [d for d in pool if float(d.get("created_at") or 0) <= before_ts + 120]
        if earlier:
            pool = earlier
    pool = [
        d
        for d in pool
        if d.get("user_input")
        and (d.get("asks_good") or "【聚焦目标】" in str(d.get("user_input") or ""))
    ]
    if not pool:
        return None
    ranked = sorted(
        pool, key=lambda d: (score_dialogue(d, summary, block_id), d.get("seq") or 0)
    )
    best = ranked[-1]
    if score_dialogue(best, summary, block_id) <= 1:
        return pool[-1]
    return best


def page_from_text(text: str) -> int:
    m = _PAGE_RE.search(text or "")
    return int(m.group(1)) if m else 0


def locus_block_id(locus_key: str, claim: dict, concrete: dict) -> str:
    for src in (
        (claim.get("causal_chain") or {}).get("block_id")
        if isinstance(claim.get("causal_chain"), dict)
        else None,
        (concrete.get("causal_chain") or {}).get("block_id")
        if isinstance(concrete.get("causal_chain"), dict)
        else None,
        concrete.get("block_id"),
        claim.get("block_id"),
    ):
        s = str(src or "").strip()
        if s:
            return s
    lk = str(locus_key or "")
    # common shapes: page:60:block:xxx / block=xxx
    m = re.search(r"(?:block[_:]|:)([0-9a-f]{8,})", lk, re.I)
    if m:
        return m.group(1)
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument(
        "--copilot-db",
        default=str(ROOT / "buddy-copilot" / "data" / "copilot.db"),
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing user_input (needed to fix wrong matches)",
    )
    args = ap.parse_args()

    turns = load_user_turns(Path(args.copilot_db), args.session)
    by_page = dialogues_by_page(turns)
    print(
        f"session_user_turns={len(turns)} pages_with_dialogue={len(by_page)} "
        f"dialogs={sum(len(v) for v in by_page.values())} "
        f"good_dialogs={sum(1 for v in by_page.values() for d in v if d.get('has_problem'))}"
    )

    list_script = r"""
import json
from app.db.session import get_session_factory
from app.db.models import CorpusImprovementPoint
from app.util.json_util import loads
s = get_session_factory()()
try:
    out=[]
    for r in s.query(CorpusImprovementPoint).all():
        claim=loads(r.claim_json,{}) or {}
        concrete=loads(r.concrete_json,{}) or {}
        out.append({
            "id": r.id,
            "page_no": int(r.page_no or 0),
            "locus_key": r.locus_key or "",
            "source_session_id": r.source_session_id or "",
            "summary": (claim.get("summary") or ""),
            "origin": claim.get("origin") or concrete.get("origin") or "",
            "confirmed_at": (
                (concrete.get("confirmed_at") or claim.get("confirmed_at")
                 or concrete.get("at") or "")
            ),
            "user_input": str(claim.get("user_input") or concrete.get("user_input") or ""),
            "claim": claim,
            "concrete": concrete,
        })
    print(json.dumps(out, ensure_ascii=False))
finally:
    s.close()
"""
    proc = subprocess.run(
        ["docker", "exec", "buddy-api", "python", "-c", list_script],
        capture_output=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode())
        raise SystemExit(proc.returncode)
    items = json.loads(proc.stdout.decode())

    con = sqlite3.connect(str(Path(args.copilot_db)))
    pl = json.loads(
        con.execute(
            "SELECT payload FROM sessions WHERE id=?", (args.session,)
        ).fetchone()[0]
    )
    con.close()
    ec_by_sum: dict[str, float] = {}
    for e in pl.get("effective_changes") or []:
        if not isinstance(e, dict):
            continue
        s = str(e.get("summary") or "").strip()[:80]
        ts = _iso_to_ts(str(e.get("confirmed_at") or ""))
        if s and ts:
            ec_by_sum[s] = ts

    patches = []
    cleared = 0
    skipped = 0
    for it in items:
        if str(it.get("source_session_id") or "") != args.session:
            continue
        if str(it.get("origin") or "") == "fullbook_ai":
            continue
        page = int(it.get("page_no") or 0)
        if page <= 0:
            skipped += 1
            continue
        old_ui = str(it.get("user_input") or "")
        old_page_in_ui = page_from_text(old_ui)
        wrong = bool(old_ui) and old_page_in_ui > 0 and old_page_in_ui != page
        control = bool(old_ui) and is_control(strip_focus(old_ui) or old_ui)
        if old_ui and not args.force and not wrong and not control:
            continue

        summary = str(it.get("summary") or "")
        before = _iso_to_ts(str(it.get("confirmed_at") or ""))
        if not before:
            before = ec_by_sum.get(summary[:80], 0.0)
        bid = locus_block_id(
            str(it.get("locus_key") or ""),
            it.get("claim") or {},
            it.get("concrete") or {},
        )

        dlg = pick_dialogue(
            by_page.get(page) or [],
            summary=summary,
            block_id=bid,
            before_ts=before,
        )

        cc = dict(it.get("claim", {}).get("causal_chain") or {})
        if not isinstance(cc, dict):
            cc = {}
        # preserve non-user causal fields
        if not dlg or not dlg.get("user_input"):
            # Clear cross-page / invented user_input when page has no real dialogue
            if old_ui or wrong:
                cc.update(
                    {
                        "user_requests": [],
                        "asks_good": [],
                        "page_no": page,
                        "user_input_source": "none_no_page_dialogue",
                        "dialogue_seq": None,
                        "block_id": bid,
                    }
                )
                patches.append(
                    {
                        "id": it["id"],
                        "user_input": "",
                        "clear": True,
                        "causal_chain": cc,
                        "page_no": page,
                        "old_user": old_ui[:80],
                        "reason": "clear_no_dialogue",
                    }
                )
                cleared += 1
            else:
                skipped += 1
            continue

        ui = str(dlg.get("user_input_raw") or dlg["user_input"])  # FULL raw
        if page_from_text(ui) not in (0, page):
            skipped += 1
            continue

        raw_list = list(dlg.get("user_requests_raw") or dlg.get("user_requests") or [])
        cc.update(
            {
                "user_requests": raw_list,
                "user_requests_raw": raw_list,
                "asks_good": list(dlg.get("asks_good") or []),
                "page_no": page,
                "block_id": str(dlg.get("block_id") or bid or ""),
                "user_input_source": "session_focus_rematch_v4_raw",
                "dialogue_seq": int(dlg.get("seq") or 0),
                "user_input_raw": ui,
                "user_input_free": str(dlg.get("user_input_free") or ""),
            }
        )
        patches.append(
            {
                "id": it["id"],
                "user_input": ui,
                "user_input_raw": ui,
                "clear": False,
                "causal_chain": cc,
                "page_no": page,
                "old_user": old_ui[:80],
                "reason": "wrong_page"
                if wrong
                else ("control" if control else ("force" if args.force else "fill")),
            }
        )

    print(f"to_patch={len(patches)} cleared={cleared} skipped={skipped}")
    if args.dry_run:
        for p in patches[:15]:
            print(
                json.dumps(
                    {
                        "id": p["id"][:8],
                        "page": p["page_no"],
                        "reason": p["reason"],
                        "old": p["old_user"],
                        "new_len": len(p.get("user_input") or ""),
                        "new": (p.get("user_input") or "")[:120],
                    },
                    ensure_ascii=False,
                )
            )
        print("dry-run done")
        return

    apply_script = r"""
import json, sys
from app.db.session import get_session_factory
from app.db.models import CorpusImprovementPoint
from app.util.json_util import dumps, loads
patches=json.loads(sys.stdin.read())
n=0
s=get_session_factory()()
try:
    for p in patches:
        row=s.get(CorpusImprovementPoint, p["id"])
        if not row: continue
        claim=loads(row.claim_json,{}) or {}
        concrete=loads(row.concrete_json,{}) or {}
        if not isinstance(claim, dict): claim={}
        if not isinstance(concrete, dict): concrete={}
        ui=p.get("user_input") or ""
        if p.get("clear") or not ui:
            claim.pop("user_input", None)
            claim.pop("user_input_raw", None)
            concrete.pop("user_input", None)
            concrete.pop("user_input_raw", None)
        else:
            claim["user_input"]=ui
            claim["user_input_raw"]=p.get("user_input_raw") or ui
            concrete["user_input"]=ui
            concrete["user_input_raw"]=claim["user_input_raw"]
        claim["causal_chain"]=p.get("causal_chain") or {}
        concrete["causal_chain"]=claim["causal_chain"]
        row.claim_json=dumps(claim)
        row.concrete_json=dumps(concrete)
        s.add(row)
        n+=1
    s.commit()
    print(f"updated {n}")
except Exception:
    s.rollback()
    raise
finally:
    s.close()
"""
    proc = subprocess.run(
        ["docker", "exec", "-i", "buddy-api", "python", "-c", apply_script],
        input=json.dumps(patches, ensure_ascii=False).encode(),
        capture_output=True,
    )
    sys.stdout.write(proc.stdout.decode())
    sys.stderr.write(proc.stderr.decode())
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


if __name__ == "__main__":
    main()

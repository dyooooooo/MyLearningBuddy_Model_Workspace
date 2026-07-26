"""会话与审计存储（SQLite，WAL 模式）。

消息表即 Studio 轮询的事件流：每条消息带会话内递增 seq，
前端用 after_seq 增量拉取。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT '',
    import_job_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT,
    plan TEXT,
    iteration INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    action_key TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    note TEXT NOT NULL DEFAULT '',
    remember INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS session_counters (
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, name)
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            # 迁移：旧库补列
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
            if "model" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN model TEXT NOT NULL DEFAULT ''")
            if "import_job_id" not in cols:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN import_job_id TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_import_job"
                " ON sessions(import_job_id)"
            )
            acols = {r["name"] for r in conn.execute("PRAGMA table_info(approvals)")}
            if "note" not in acols:
                conn.execute("ALTER TABLE approvals ADD COLUMN note TEXT NOT NULL DEFAULT ''")
            if "remember" not in acols:
                conn.execute(
                    "ALTER TABLE approvals ADD COLUMN remember INTEGER NOT NULL DEFAULT 0"
                )
            # 回填：从 payload JSON 抽 import_job_id / job_id
            for row in conn.execute(
                "SELECT id, payload FROM sessions"
                " WHERE COALESCE(import_job_id, '') = ''"
            ).fetchall():
                jid = self._job_id_from_payload_text(row["payload"] or "{}")
                if jid:
                    conn.execute(
                        "UPDATE sessions SET import_job_id=? WHERE id=?",
                        (jid, row["id"]),
                    )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _job_id_from_payload_text(payload_text: str) -> str:
        try:
            pl = json.loads(payload_text or "{}")
        except json.JSONDecodeError:
            return ""
        if not isinstance(pl, dict):
            return ""
        return str(pl.get("import_job_id") or pl.get("job_id") or "").strip()

    @staticmethod
    def _job_id_from_payload(payload: dict[str, Any] | None) -> str:
        if not isinstance(payload, dict):
            return ""
        return str(payload.get("import_job_id") or payload.get("job_id") or "").strip()

    # ---- sessions ----

    def create_session(self, kind: str, payload: dict[str, Any], model: str = "") -> str:
        sid = uuid.uuid4().hex[:16]
        now = time.time()
        jid = self._job_id_from_payload(payload)
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions"
                " (id, kind, status, payload, model, import_job_id, created_at, updated_at)"
                " VALUES (?, ?, 'planning', ?, ?, ?, ?, ?)",
                (
                    sid,
                    kind,
                    json.dumps(payload, ensure_ascii=False),
                    model,
                    jid,
                    now,
                    now,
                ),
            )
        return sid

    def get_session(self, sid: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"] or "{}")
        d["plan"] = json.loads(d["plan"]) if d.get("plan") else None
        return d

    @staticmethod
    def _label_from_payload_text(payload_text: str) -> str:
        """会话列表展示名：优先 label，其次 PDF 文件名。"""
        try:
            pl = json.loads(payload_text or "{}")
        except json.JSONDecodeError:
            return ""
        if not isinstance(pl, dict):
            return ""
        label = str(pl.get("label") or "").strip()
        if label:
            return label
        key = str(pl.get("minio_key") or "").strip().replace("\\", "/")
        if key:
            return key.rsplit("/", 1)[-1]
        page_no = pl.get("page_no")
        return f"页 {page_no}" if page_no else ""

    def list_sessions(
        self, limit: int = 50, *, import_job_id: str = ""
    ) -> list[dict[str, Any]]:
        jid = (import_job_id or "").strip()
        cols = "id, kind, status, import_job_id, created_at, updated_at, payload"
        with self._conn() as conn:
            if jid:
                rows = conn.execute(
                    f"SELECT {cols} FROM sessions WHERE import_job_id=?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (jid, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {cols} FROM sessions ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["label"] = self._label_from_payload_text(d.pop("payload", "") or "{}")
            out.append(d)
        return out

    def find_session_id_by_import_job(self, import_job_id: str) -> str | None:
        """同一导入任务只应有一条 AI 会话；取最新一条。"""
        jid = (import_job_id or "").strip()
        if not jid:
            return None
        rows = self.list_sessions(limit=1, import_job_id=jid)
        return str(rows[0]["id"]) if rows else None

    def bind_import_job(self, sid: str, import_job_id: str) -> list[str]:
        """将会话绑定到导入任务；删除同 job 下其它会话，返回被删 id 列表。"""
        jid = (import_job_id or "").strip()
        if not jid:
            return []
        removed: list[str] = []
        with self._lock, self._conn() as conn:
            others = conn.execute(
                "SELECT id FROM sessions WHERE import_job_id=? AND id!=?",
                (jid, sid),
            ).fetchall()
            for row in others:
                oid = str(row["id"])
                self._delete_session_locked(conn, oid)
                removed.append(oid)
            # 同步 payload 内字段
            row = conn.execute("SELECT payload FROM sessions WHERE id=?", (sid,)).fetchone()
            if row:
                try:
                    pl = json.loads(row["payload"] or "{}")
                except json.JSONDecodeError:
                    pl = {}
                if not isinstance(pl, dict):
                    pl = {}
                pl["import_job_id"] = jid
                pl.setdefault("job_id", jid)
                conn.execute(
                    "UPDATE sessions SET import_job_id=?, payload=?, updated_at=?"
                    " WHERE id=?",
                    (jid, json.dumps(pl, ensure_ascii=False), time.time(), sid),
                )
        return removed

    def delete_session(self, sid: str) -> bool:
        """物理删除会话及其消息/批准/审计。"""
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row:
                return False
            self._delete_session_locked(conn, sid)
        return True

    def delete_sessions_by_import_job(self, import_job_id: str) -> list[str]:
        jid = (import_job_id or "").strip()
        if not jid:
            return []
        removed: list[str] = []
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM sessions WHERE import_job_id=?",
                (jid,),
            ).fetchall()
            for row in rows:
                oid = str(row["id"])
                self._delete_session_locked(conn, oid)
                removed.append(oid)
        return removed

    def _delete_session_locked(self, conn: sqlite3.Connection, sid: str) -> None:
        conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        conn.execute("DELETE FROM approvals WHERE session_id=?", (sid,))
        conn.execute("DELETE FROM audit_log WHERE session_id=?", (sid,))
        conn.execute("DELETE FROM sessions WHERE id=?", (sid,))

    def update_session(self, sid: str, **fields: Any) -> None:
        allowed = {"status", "agent_id", "plan", "iteration", "payload", "import_job_id"}
        sets, vals = [], []
        sync_job_from_payload: str | None = None
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"unknown session field: {k}")
            if k == "payload":
                if not isinstance(v, (str, type(None))):
                    sync_job_from_payload = self._job_id_from_payload(v)
                    v = json.dumps(v, ensure_ascii=False)
                else:
                    sync_job_from_payload = self._job_id_from_payload_text(v or "{}")
            if k == "plan" and not isinstance(v, (str, type(None))):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k}=?")
            vals.append(v)
        if sync_job_from_payload and "import_job_id" not in fields:
            sets.append("import_job_id=?")
            vals.append(sync_job_from_payload)
        sets.append("updated_at=?")
        vals.append(time.time())
        vals.append(sid)
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id=?", vals)

    # ---- messages ----

    def add_message(self, sid: str, role: str, content: dict[str, Any]) -> int:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS mx FROM messages WHERE session_id=?",
                (sid,),
            ).fetchone()
            seq = int(row["mx"]) + 1
            conn.execute(
                "INSERT INTO messages (session_id, seq, role, content, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (sid, seq, role, json.dumps(content, ensure_ascii=False), time.time()),
            )
        return seq

    def last_message(self, sid: str) -> dict[str, Any] | None:
        """最新一条消息（空转看门狗用：判断会话最近是否有活动）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT seq, role, content, created_at FROM messages"
                " WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                (sid,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["content"] = json.loads(d["content"])
        return d

    def has_pending_approval(self, sid: str) -> bool:
        """会话是否有待决批准卡（等审批期间不算空转）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM approvals WHERE session_id=? AND status='pending' LIMIT 1",
                (sid,),
            ).fetchone()
        return row is not None

    def get_messages(self, sid: str, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT seq, role, content, created_at FROM messages"
                " WHERE session_id=? AND seq>? ORDER BY seq",
                (sid, after_seq),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["content"] = json.loads(d["content"])
            out.append(d)
        return out

    # ---- approvals ----

    def create_approval(self, sid: str, action_key: str, action: dict[str, Any]) -> str:
        aid = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO approvals (id, session_id, action_key, action, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (aid, sid, action_key, json.dumps(action, ensure_ascii=False), time.time()),
            )
        return aid

    def get_approval(self, aid: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["action"] = json.loads(d["action"])
        return d

    def decide_approval(self, aid: str, approved: bool, note: str = "", remember: bool = False) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status=?, note=?, remember=?, decided_at=?"
                " WHERE id=? AND status='pending'",
                (
                    "approved" if approved else "rejected",
                    note,
                    1 if remember else 0,
                    time.time(),
                    aid,
                ),
            )
        return cur.rowcount > 0

    def has_session_allow(self, sid: str, action_key: str) -> bool:
        """本会话内该动作是否已被「总是允许」（approved + remember）。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM approvals WHERE session_id=? AND action_key=?"
                " AND status='approved' AND remember=1 LIMIT 1",
                (sid, action_key),
            ).fetchone()
        return row is not None

    def list_approvals(self, sid: str, action_key: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM approvals WHERE session_id=?"
        args: list[Any] = [sid]
        if action_key:
            q += " AND action_key=?"
            args.append(action_key)
        with self._conn() as conn:
            rows = conn.execute(q + " ORDER BY created_at", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["action"] = json.loads(d["action"])
            out.append(d)
        return out

    # ---- counters ----

    def bump_counter(self, sid: str, name: str, step: int = 1) -> int:
        """按会话累加计数器并返回新值。

        工具调用预算必须落库：一个会话可能同时挂着多个 MCP 进程，
        进程内计数会被各算一份，预算等于形同虚设。
        """
        if not sid:
            return 0
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO session_counters (session_id, name, value) VALUES (?, ?, ?)"
                " ON CONFLICT(session_id, name) DO UPDATE SET value = value + excluded.value",
                (sid, name, int(step)),
            )
            row = conn.execute(
                "SELECT value FROM session_counters WHERE session_id=? AND name=?",
                (sid, name),
            ).fetchone()
        return int(row["value"]) if row else 0

    def reset_counter(self, sid: str, name: str) -> None:
        if not sid:
            return
        with self._lock, self._conn() as conn:
            conn.execute(
                "DELETE FROM session_counters WHERE session_id=? AND name=?",
                (sid, name),
            )

    def get_counter(self, sid: str, name: str) -> int:
        if not sid:
            return 0
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM session_counters WHERE session_id=? AND name=?",
                (sid, name),
            ).fetchone()
        return int(row["value"]) if row else 0

    # ---- audit ----

    def audit(self, sid: str | None, actor: str, action: str, detail: dict[str, Any]) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (session_id, ts, actor, action, detail)"
                " VALUES (?, ?, ?, ?, ?)",
                (sid, time.time(), actor, action, json.dumps(detail, ensure_ascii=False)),
            )

    def get_audit(self, sid: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, actor, action, detail FROM audit_log"
                " WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (sid, limit),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"])
            out.append(d)
        return out

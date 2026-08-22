"""SQLite 저장소: 분석 리포트와 칸반 업무."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config

VALID_STATUSES = ("suggested", "todo", "in_progress", "done")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                summary TEXT NOT NULL,
                insights TEXT NOT NULL DEFAULT '[]',
                data_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_profile (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL DEFAULT '나의 분석가',
                instructions TEXT NOT NULL DEFAULT '',
                lessons TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                ref_id INTEGER,
                signal TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                user_name TEXT NOT NULL DEFAULT '',
                consumed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'medium',
                category TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'suggested',
                assignee TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


# ── 에이전트 프로필 ─────────────────────────────────────

def get_agent_profile() -> dict:
    """프로필 단일 행을 반환 (없으면 기본값으로 생성)."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_profile WHERE id = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO agent_profile (id, name, instructions, lessons, updated_at) "
                "VALUES (1, '나의 분석가', '', '', ?)",
                (_now(),),
            )
            row = conn.execute("SELECT * FROM agent_profile WHERE id = 1").fetchone()
        return dict(row)


def update_agent_profile(name: str | None = None, instructions: str | None = None,
                         lessons: str | None = None) -> dict:
    get_agent_profile()
    updates = {
        k: v
        for k, v in (("name", name), ("instructions", instructions), ("lessons", lessons))
        if v is not None
    }
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(f"UPDATE agent_profile SET {set_clause} WHERE id = 1", (*updates.values(),))
    return get_agent_profile()


# ── 피드백 ──────────────────────────────────────────────

def add_feedback(kind: str, signal: str, ref_id: int | None = None, comment: str = "",
                 context: str = "", user_name: str = "") -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO feedback (kind, ref_id, signal, comment, context, user_name, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (kind, ref_id, signal, comment, context, user_name, _now()),
        )
        row = conn.execute("SELECT * FROM feedback WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_feedback(limit: int = 50, unconsumed_only: bool = False) -> list[dict]:
    q = "SELECT * FROM feedback"
    if unconsumed_only:
        q += " WHERE consumed = 0"
    q += " ORDER BY id DESC LIMIT ?"
    with get_conn() as conn:
        rows = conn.execute(q, (limit,)).fetchall()
        return [dict(r) for r in rows]


def count_unconsumed_feedback() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM feedback WHERE consumed = 0").fetchone()[0]


def mark_feedback_consumed(ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with get_conn() as conn:
        conn.execute(f"UPDATE feedback SET consumed = 1 WHERE id IN ({placeholders})", ids)


# ── 사용자 ──────────────────────────────────────────────

def create_user(username: str, name: str, password_hash: str, role: str = "member") -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, name, password_hash, role, created_at) VALUES (?,?,?,?,?)",
            (username, name, password_hash, role, _now()),
        )
        row = conn.execute(
            "SELECT id, username, name, role, created_at FROM users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def get_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, name, role, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def count_users() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def count_admins() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]


def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, name, role, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_user(user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def set_user_password(user_id: int, password_hash: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        # 비밀번호 변경 시 기존 세션 전부 무효화
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ── 세션 ────────────────────────────────────────────────

def create_session(token: str, user_id: int, expires_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?,?,?,?)",
            (token, user_id, expires_at, _now()),
        )


def get_session_user(token: str) -> dict | None:
    """유효한 세션이면 사용자 정보를 반환하고, 만료된 세션은 정리한다."""
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
        row = conn.execute(
            """SELECT u.id, u.username, u.name, u.role
               FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at >= ?""",
            (token, _now()),
        ).fetchone()
        return dict(row) if row else None


def delete_session(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ── 리포트 ──────────────────────────────────────────────

def create_report(run_date: str, summary: str, insights: list[str], data_notes: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reports (run_date, summary, insights, data_notes, created_at) VALUES (?,?,?,?,?)",
            (run_date, summary, json.dumps(insights, ensure_ascii=False), data_notes, _now()),
        )
        return cur.lastrowid


def _report_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["insights"] = json.loads(d["insights"])
    return d


def get_latest_report() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 1").fetchone()
        return _report_row(row) if row else None


def list_reports(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_report_row(r) for r in rows]


# ── 업무 ────────────────────────────────────────────────

def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    category: str = "",
    status: str = "todo",
    assignee: str = "",
    source: str = "manual",
    report_id: int | None = None,
) -> dict:
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (report_id, title, description, priority, category,
                                  status, assignee, source, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (report_id, title, description, priority, category, status, assignee, source, now, now),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_tasks() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_task(task_id: int, fields: dict) -> dict | None:
    allowed = {"title", "description", "priority", "category", "status", "assignee"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "status" in updates and updates["status"] not in VALID_STATUSES:
        raise ValueError(f"잘못된 상태값: {updates['status']}")
    if not updates:
        return get_task(task_id)
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?",
            (*updates.values(), task_id),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def get_task(task_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


def delete_task(task_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0

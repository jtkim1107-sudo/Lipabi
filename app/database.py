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
                team TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS user_agents (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                lessons TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS briefings (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                run_date TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, run_date)
            );
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                task_id INTEGER,
                task_title TEXT NOT NULL DEFAULT '',
                user_name TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                user_name TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                is_blocker INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
                run_date TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS data_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER REFERENCES reports(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                suggested_dax TEXT NOT NULL DEFAULT '',
                blocking INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
                blocked INTEGER NOT NULL DEFAULT 0,
                suggested_assignee TEXT NOT NULL DEFAULT '',
                deliverable TEXT NOT NULL DEFAULT '',
                deliverable_summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """기존 DB에 새 컬럼을 추가하는 경량 마이그레이션."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(feedback)")}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE feedback ADD COLUMN user_id INTEGER")
    if "personal_consumed" not in cols:
        conn.execute(
            "ALTER TABLE feedback ADD COLUMN personal_consumed INTEGER NOT NULL DEFAULT 0"
        )
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "team" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN team TEXT NOT NULL DEFAULT ''")
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    for col in ("suggested_assignee", "deliverable", "deliverable_summary"):
        if col not in task_cols:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    dr_cols = {r[1] for r in conn.execute("PRAGMA table_info(data_requests)")}
    if "blocking" not in dr_cols:
        conn.execute("ALTER TABLE data_requests ADD COLUMN blocking INTEGER NOT NULL DEFAULT 0")
    task_cols2 = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "blocked" not in task_cols2:
        conn.execute("ALTER TABLE tasks ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")


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


# ── 개인 에이전트 ───────────────────────────────────────

def get_user_agent(user_id: int, default_name: str = "나의 에이전트") -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM user_agents WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO user_agents (user_id, name, instructions, lessons, updated_at) "
                "VALUES (?,?,'','',?)",
                (user_id, default_name, _now()),
            )
            row = conn.execute(
                "SELECT * FROM user_agents WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(row)


def update_user_agent(user_id: int, name: str | None = None, instructions: str | None = None,
                      lessons: str | None = None) -> dict:
    get_user_agent(user_id)
    updates = {
        k: v
        for k, v in (("name", name), ("instructions", instructions), ("lessons", lessons))
        if v is not None
    }
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE user_agents SET {set_clause} WHERE user_id = ?",
            (*updates.values(), user_id),
        )
    return get_user_agent(user_id)


# ── 브리핑 캐시 ─────────────────────────────────────────

def get_briefing(user_id: int, run_date: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content FROM briefings WHERE user_id = ? AND run_date = ?",
            (user_id, run_date),
        ).fetchone()
        return row["content"] if row else None


def save_briefing(user_id: int, run_date: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO briefings (user_id, run_date, content, created_at)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id, run_date) DO UPDATE SET content = excluded.content,
                                                            created_at = excluded.created_at""",
            (user_id, run_date, content, _now()),
        )


# ── 피드백 ──────────────────────────────────────────────

def add_feedback(kind: str, signal: str, ref_id: int | None = None, comment: str = "",
                 context: str = "", user_name: str = "", user_id: int | None = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO feedback (kind, ref_id, signal, comment, context, user_name,
                                     user_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (kind, ref_id, signal, comment, context, user_name, user_id, _now()),
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


def list_personal_feedback(user_id: int, limit: int = 200,
                           unconsumed_only: bool = False) -> list[dict]:
    q = "SELECT * FROM feedback WHERE user_id = ?"
    if unconsumed_only:
        q += " AND personal_consumed = 0"
    q += " ORDER BY id DESC LIMIT ?"
    with get_conn() as conn:
        rows = conn.execute(q, (user_id, limit)).fetchall()
        return [dict(r) for r in rows]


def count_unconsumed_personal_feedback(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE user_id = ? AND personal_consumed = 0",
            (user_id,),
        ).fetchone()[0]


def mark_personal_consumed(ids: list[int]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" * len(ids))
    with get_conn() as conn:
        conn.execute(
            f"UPDATE feedback SET personal_consumed = 1 WHERE id IN ({placeholders})", ids
        )


# ── 사용자 ──────────────────────────────────────────────

VALID_ROLES = ("admin", "leader", "member")


def create_user(username: str, name: str, password_hash: str, role: str = "member",
                team: str = "") -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, name, password_hash, role, team, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (username, name, password_hash, role, team, _now()),
        )
        row = conn.execute(
            "SELECT id, username, name, role, team, created_at FROM users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


def update_user(user_id: int, fields: dict) -> dict | None:
    allowed = {"name", "role", "team"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_user(user_id)
    with get_conn() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (*updates.values(), user_id))
    return get_user(user_id)


def get_team_members(team: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, name, role, team FROM users WHERE team = ? ORDER BY id",
            (team,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_by_username(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, username, name, role, team, created_at FROM users ORDER BY id"
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
            "SELECT id, username, name, role, team, created_at FROM users WHERE id = ?",
            (user_id,),
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
            """SELECT u.id, u.username, u.name, u.role, u.team
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


# ── 활동 로그 ───────────────────────────────────────────

def log_activity(day: str, kind: str, task_id: int | None = None, task_title: str = "",
                 user_name: str = "", detail: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO activities (day, task_id, task_title, user_name, kind, detail, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (day, task_id, task_title, user_name, kind, detail, _now()),
        )


def list_activities(day: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activities WHERE day = ? ORDER BY id", (day,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── 업무 코멘트 (진행 메모 / 막힘) ──────────────────────

def add_task_comment(task_id: int, user_name: str, text: str, is_blocker: bool = False) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO task_comments (task_id, user_name, text, is_blocker, created_at) "
            "VALUES (?,?,?,?,?)",
            (task_id, user_name, text, int(is_blocker), _now()),
        )
        if is_blocker:
            conn.execute(
                "UPDATE tasks SET blocked = 1, updated_at = ? WHERE id = ?",
                (_now(), task_id),
            )
        row = conn.execute(
            "SELECT * FROM task_comments WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def list_task_comments(task_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM task_comments WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def set_task_blocked(task_id: int, blocked: bool) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET blocked = ?, updated_at = ? WHERE id = ?",
            (int(blocked), _now(), task_id),
        )
    return get_task(task_id)


# ── 업무 보고 ───────────────────────────────────────────

def save_work_report(run_date: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO work_reports (run_date, content, created_at) VALUES (?,?,?)",
            (run_date, content, _now()),
        )
        return cur.lastrowid


def get_latest_work_report() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["content"] = json.loads(d["content"])
        return d


# ── 자기 채점 ───────────────────────────────────────────

def save_review(report_id: int, run_date: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reviews (report_id, run_date, content, created_at) VALUES (?,?,?,?)",
            (report_id, run_date, content, _now()),
        )
        return cur.lastrowid


def get_latest_review() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        d = dict(row)
        d["content"] = json.loads(d["content"])
        return d


# ── 데이터 요청 ─────────────────────────────────────────

DATA_REQUEST_STATUSES = ("open", "tasked", "resolved", "dismissed")


def create_data_request(title: str, reason: str = "", suggested_dax: str = "",
                        report_id: int | None = None, blocking: bool = False) -> dict:
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO data_requests (report_id, title, reason, suggested_dax,
                                          blocking, status, created_at, updated_at)
               VALUES (?,?,?,?,?, 'open', ?, ?)""",
            (report_id, title, reason, suggested_dax, int(blocking), now, now),
        )
        row = conn.execute(
            "SELECT * FROM data_requests WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


def list_data_requests(statuses: tuple[str, ...] | None = None) -> list[dict]:
    q = "SELECT * FROM data_requests"
    params: tuple = ()
    if statuses:
        q += f" WHERE status IN ({','.join('?' * len(statuses))})"
        params = statuses
    q += " ORDER BY id DESC"
    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]


def get_data_request(request_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM data_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None


def find_active_request_by_title(title: str) -> dict | None:
    """이미 열려 있거나 업무화된 동일 제목의 요청 (중복 방지용)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM data_requests WHERE title = ? AND status IN ('open','tasked')",
            (title,),
        ).fetchone()
        return dict(row) if row else None


def set_data_request_status(request_id: int, status: str) -> dict | None:
    if status not in DATA_REQUEST_STATUSES:
        raise ValueError(f"잘못된 상태값: {status}")
    with get_conn() as conn:
        conn.execute(
            "UPDATE data_requests SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), request_id),
        )
    return get_data_request(request_id)


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
    suggested_assignee: str = "",
) -> dict:
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO tasks (report_id, title, description, priority, category,
                                  status, assignee, source, suggested_assignee,
                                  created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (report_id, title, description, priority, category, status, assignee, source,
             suggested_assignee, now, now),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def set_task_deliverable(task_id: int, deliverable: str, summary: str) -> dict | None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE tasks SET deliverable = ?, deliverable_summary = ?,
                                status = CASE WHEN status IN ('suggested','todo')
                                              THEN 'in_progress' ELSE status END,
                                updated_at = ?
               WHERE id = ?""",
            (deliverable, summary, _now(), task_id),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


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

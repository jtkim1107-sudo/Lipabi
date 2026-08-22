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

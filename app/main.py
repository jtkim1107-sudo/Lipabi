"""칸반보드 웹 서버 + 매일 아침 자동 분석 스케줄러 + 로그인.

실행: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, config, database, pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "session"

scheduler = BackgroundScheduler(timezone=config.TIMEZONE)


def _scheduled_run():
    try:
        result = pipeline.run_daily_pipeline()
        logger.info("자동 분석 완료: %s", result["summary"][:100])
    except Exception:
        logger.exception("자동 분석 실패")


def _ensure_admin():
    """사용자가 한 명도 없으면 관리자 계정을 만든다."""
    if database.count_users() > 0:
        return
    password = config.ADMIN_PASSWORD or secrets.token_urlsafe(9)
    database.create_user(
        username=config.ADMIN_USERNAME,
        name=config.ADMIN_NAME,
        password_hash=auth.hash_password(password),
        role="admin",
    )
    if config.ADMIN_PASSWORD:
        logger.info("관리자 계정 '%s' 생성됨 (.env의 ADMIN_PASSWORD 사용)", config.ADMIN_USERNAME)
    else:
        # 최초 1회만 출력됨 — 로그인 후 반드시 비밀번호를 변경할 것
        logger.warning(
            "관리자 계정이 생성되었습니다 → 아이디: %s / 임시 비밀번호: %s",
            config.ADMIN_USERNAME,
            password,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    _ensure_admin()
    hour, minute = config.DAILY_RUN_TIME.split(":")
    scheduler.add_job(
        _scheduled_run,
        CronTrigger(hour=int(hour), minute=int(minute)),
        id="daily_analysis",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("매일 %s (%s) 자동 분석 예약됨", config.DAILY_RUN_TIME, config.TIMEZONE)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Lipabi — Power BI AI 분석 칸반보드", lifespan=lifespan)


# ── 인증 ────────────────────────────────────────────────

def get_current_user(session: str | None = Cookie(default=None)) -> dict | None:
    if not session:
        return None
    return database.get_session_user(session)


def require_user(user: dict | None = Depends(get_current_user)) -> dict:
    if user is None:
        raise HTTPException(401, "로그인이 필요합니다")
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "관리자 권한이 필요합니다")
    return user


class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def api_login(body: LoginBody, request: Request, response: Response):
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{body.username}"
    if auth.is_locked_out(key):
        raise HTTPException(429, "로그인 시도가 너무 많습니다. 15분 후 다시 시도하세요.")

    user = database.get_user_by_username(body.username.strip())
    if not user or not auth.verify_password(body.password, user["password_hash"]):
        auth.record_failure(key)
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")

    auth.clear_failures(key)
    token = auth.new_session_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    ).isoformat()
    database.create_session(token, user["id"], expires_at)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )
    return {"id": user["id"], "username": user["username"], "name": user["name"], "role": user["role"]}


@app.post("/api/logout")
def api_logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        database.delete_session(session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/me")
def api_me(user: dict = Depends(require_user)):
    return user


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/me/password")
def api_change_my_password(body: PasswordChangeBody, user: dict = Depends(require_user)):
    full = database.get_user_by_username(user["username"])
    if not auth.verify_password(body.current_password, full["password_hash"]):
        raise HTTPException(401, "현재 비밀번호가 올바르지 않습니다")
    if len(body.new_password) < 8:
        raise HTTPException(400, "새 비밀번호는 8자 이상이어야 합니다")
    database.set_user_password(user["id"], auth.hash_password(body.new_password))
    return {"ok": True}


# ── 직원 관리 (관리자) ──────────────────────────────────

class UserCreateBody(BaseModel):
    username: str
    name: str
    password: str
    role: str = "member"


@app.get("/api/users")
def api_list_users(_: dict = Depends(require_admin)):
    return database.list_users()


@app.post("/api/users")
def api_create_user(body: UserCreateBody, _: dict = Depends(require_admin)):
    username = body.username.strip().lower()
    if not username or not body.name.strip():
        raise HTTPException(400, "아이디와 이름을 입력하세요")
    if len(body.password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다")
    if body.role not in ("admin", "member"):
        raise HTTPException(400, "역할은 admin 또는 member만 가능합니다")
    if database.get_user_by_username(username):
        raise HTTPException(409, "이미 존재하는 아이디입니다")
    return database.create_user(
        username=username,
        name=body.name.strip(),
        password_hash=auth.hash_password(body.password),
        role=body.role,
    )


@app.delete("/api/users/{user_id}")
def api_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    target = database.get_user(user_id)
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")
    if target["id"] == admin["id"]:
        raise HTTPException(400, "자기 자신은 삭제할 수 없습니다")
    if target["role"] == "admin" and database.count_admins() <= 1:
        raise HTTPException(400, "마지막 관리자는 삭제할 수 없습니다")
    database.delete_user(user_id)
    return {"ok": True}


class PasswordResetBody(BaseModel):
    new_password: str


@app.post("/api/users/{user_id}/password")
def api_reset_password(user_id: int, body: PasswordResetBody, _: dict = Depends(require_admin)):
    if not database.get_user(user_id):
        raise HTTPException(404, "사용자를 찾을 수 없습니다")
    if len(body.new_password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다")
    database.set_user_password(user_id, auth.hash_password(body.new_password))
    return {"ok": True}


# ── 페이지 ──────────────────────────────────────────────

@app.get("/")
def index(user: dict | None = Depends(get_current_user)):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
def login_page(user: dict | None = Depends(get_current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=302)
    return FileResponse(STATIC_DIR / "login.html")


# ── 칸반보드 API ────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    category: str = ""
    status: str = "todo"
    assignee: str = ""


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    category: str | None = None
    status: str | None = None
    assignee: str | None = None


@app.get("/api/tasks")
def api_list_tasks(_: dict = Depends(require_user)):
    return database.list_tasks()


@app.post("/api/tasks")
def api_create_task(body: TaskCreate, _: dict = Depends(require_user)):
    if body.status not in database.VALID_STATUSES:
        raise HTTPException(400, f"잘못된 상태값: {body.status}")
    return database.create_task(**body.model_dump(), source="manual")


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: int, body: TaskUpdate, _: dict = Depends(require_user)):
    try:
        task = database.update_task(task_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if task is None:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    return task


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int, _: dict = Depends(require_user)):
    if not database.delete_task(task_id):
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    return {"ok": True}


@app.get("/api/reports/latest")
def api_latest_report(_: dict = Depends(require_user)):
    report = database.get_latest_report()
    return report or {}


@app.get("/api/reports")
def api_list_reports(_: dict = Depends(require_user)):
    return database.list_reports()


@app.post("/api/run")
def api_run_now(
    user: dict | None = Depends(get_current_user),
    x_run_token: str | None = Header(default=None),
):
    """분석 즉시 실행. 로그인 사용자 또는 RUN_TOKEN(자동화용) 인증."""
    token_ok = bool(
        config.RUN_TOKEN
        and x_run_token
        and secrets.compare_digest(x_run_token, config.RUN_TOKEN)
    )
    if user is None and not token_ok:
        raise HTTPException(401, "로그인이 필요합니다")
    try:
        return pipeline.run_daily_pipeline()
    except Exception as e:
        logger.exception("수동 분석 실패")
        raise HTTPException(500, f"분석 실패: {e}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

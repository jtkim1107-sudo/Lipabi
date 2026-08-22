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

from datetime import datetime as dt
from zoneinfo import ZoneInfo

from . import auth, briefer, coach, config, database, mailer, pipeline, reporter, worker

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


def _scheduled_work_report():
    try:
        result = reporter.run_and_save(_today())
        logger.info("저녁 업무 보고: %s", result.get("summary", result.get("message")))
        if result.get("generated"):
            recipients = [
                u for u in database.list_users() if u["role"] in ("admin", "leader")
            ]
            sent = mailer.send_work_report(recipients, result)
            if sent:
                logger.info("업무 보고 메일 %d건 발송", sent)
    except Exception:
        logger.exception("저녁 업무 보고 생성 실패")


def _scheduled_weekly_train():
    """주간 자동 학습: 전사 분석가 + 모든 개인 에이전트."""
    try:
        result = coach.train()
        logger.info("전사 분석가 주간 학습: %s", result.get("changelog", result.get("message")))
    except Exception:
        logger.exception("전사 분석가 주간 학습 실패")
    for u in database.list_users():
        try:
            coach.train_personal(u)
        except Exception:
            logger.exception("개인 에이전트 주간 학습 실패 (%s)", u["name"])


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
    if config.DAILY_REPORT_TIME.strip():
        r_hour, r_minute = config.DAILY_REPORT_TIME.split(":")
        scheduler.add_job(
            _scheduled_work_report,
            CronTrigger(hour=int(r_hour), minute=int(r_minute)),
            id="daily_work_report",
            replace_existing=True,
        )
        logger.info("매일 %s 업무 보고 예약됨", config.DAILY_REPORT_TIME)
    if config.WEEKLY_TRAIN.strip():
        try:
            day, hhmm = config.WEEKLY_TRAIN.strip().split()
            t_hour, t_minute = hhmm.split(":")
            scheduler.add_job(
                _scheduled_weekly_train,
                CronTrigger(day_of_week=day, hour=int(t_hour), minute=int(t_minute)),
                id="weekly_train",
                replace_existing=True,
            )
            logger.info("매주 %s 자동 학습 예약됨", config.WEEKLY_TRAIN)
        except ValueError:
            logger.error("WEEKLY_TRAIN 형식 오류 (예: 'mon 06:30'): %r", config.WEEKLY_TRAIN)
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
    database.set_last_login(user["id"])
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
    team: str = ""
    email: str = ""


class UserPatchBody(BaseModel):
    name: str | None = None
    role: str | None = None
    team: str | None = None
    email: str | None = None


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
    if body.role not in database.VALID_ROLES:
        raise HTTPException(400, "역할은 admin/leader/member만 가능합니다")
    if database.get_user_by_username(username):
        raise HTTPException(409, "이미 존재하는 아이디입니다")
    return database.create_user(
        username=username,
        name=body.name.strip(),
        password_hash=auth.hash_password(body.password),
        role=body.role,
        team=body.team.strip(),
        email=body.email.strip(),
    )


@app.patch("/api/users/{user_id}")
def api_patch_user(user_id: int, body: UserPatchBody, _: dict = Depends(require_admin)):
    target = database.get_user(user_id)
    if not target:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")
    if body.role is not None:
        if body.role not in database.VALID_ROLES:
            raise HTTPException(400, "역할은 admin/leader/member만 가능합니다")
        if target["role"] == "admin" and body.role != "admin" and database.count_admins() <= 1:
            raise HTTPException(400, "마지막 관리자의 역할은 변경할 수 없습니다")
    fields = body.model_dump(exclude_unset=True)
    for key in ("team", "email"):
        if key in fields and fields[key] is not None:
            fields[key] = fields[key].strip()
    return database.update_user(user_id, fields)


@app.get("/api/engagement")
def api_engagement(_: dict = Depends(require_admin)):
    """직원별 참여 현황 (최근 7일)."""
    return database.engagement_stats(days=7)


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


# ── 나의 분석가 (에이전트 프로필 / 교육) ────────────────

class AgentUpdateBody(BaseModel):
    name: str | None = None
    instructions: str | None = None


class FeedbackBody(BaseModel):
    kind: str  # 'report' | 'task'
    ref_id: int | None = None
    signal: str  # 'positive' | 'negative'
    comment: str = ""
    context: str = ""


@app.get("/api/agent")
def api_get_agent(user: dict = Depends(require_user)):
    profile = database.get_agent_profile()
    profile["pending_feedback"] = database.count_unconsumed_feedback()
    if user["role"] != "admin":
        # 일반 직원에게는 이름만 공개
        return {"name": profile["name"]}
    return profile


@app.put("/api/agent")
def api_update_agent(body: AgentUpdateBody, _: dict = Depends(require_admin)):
    return database.update_agent_profile(name=body.name, instructions=body.instructions)


@app.post("/api/agent/train")
def api_train_agent(_: dict = Depends(require_admin)):
    try:
        return coach.train()
    except Exception as e:
        logger.exception("에이전트 학습 실패")
        raise HTTPException(500, f"학습 실패: {e}")


@app.post("/api/feedback")
def api_add_feedback(body: FeedbackBody, user: dict = Depends(require_user)):
    if body.signal not in ("positive", "negative"):
        raise HTTPException(400, "signal은 positive 또는 negative여야 합니다")
    if body.kind not in ("report", "task"):
        raise HTTPException(400, "kind는 report 또는 task여야 합니다")
    return database.add_feedback(
        kind=body.kind,
        signal=body.signal,
        ref_id=body.ref_id,
        comment=body.comment.strip(),
        context=body.context.strip(),
        user_name=user["name"],
        user_id=user["id"],
    )


@app.get("/api/feedback")
def api_list_feedback(_: dict = Depends(require_admin)):
    return database.list_feedback(limit=50)


# ── 개인 에이전트 / 오늘 브리핑 ─────────────────────────

def _today() -> str:
    return dt.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")


@app.get("/api/my-agent")
def api_get_my_agent(user: dict = Depends(require_user)):
    agent = database.get_user_agent(user["id"], default_name=f"{user['name']}의 에이전트")
    agent["pending_feedback"] = database.count_unconsumed_personal_feedback(user["id"])
    return agent


@app.put("/api/my-agent")
def api_update_my_agent(body: AgentUpdateBody, user: dict = Depends(require_user)):
    return database.update_user_agent(
        user["id"], name=body.name, instructions=body.instructions
    )


@app.post("/api/my-agent/train")
def api_train_my_agent(user: dict = Depends(require_user)):
    try:
        return coach.train_personal(user)
    except Exception as e:
        logger.exception("개인 에이전트 학습 실패")
        raise HTTPException(500, f"학습 실패: {e}")


@app.get("/api/briefing")
def api_get_briefing(user: dict = Depends(require_user), refresh: bool = False):
    try:
        return briefer.get_or_create(user, _today(), refresh=refresh)
    except Exception as e:
        logger.exception("브리핑 생성 실패")
        raise HTTPException(500, f"브리핑 생성 실패: {e}")


# ── 데이터 요청 ─────────────────────────────────────────

class DataRequestStatusBody(BaseModel):
    status: str


@app.get("/api/data-requests")
def api_list_data_requests(_: dict = Depends(require_user), all: bool = False):
    statuses = None if all else ("open", "tasked")
    return database.list_data_requests(statuses=statuses)


@app.patch("/api/data-requests/{request_id}")
def api_update_data_request(
    request_id: int, body: DataRequestStatusBody, _: dict = Depends(require_admin)
):
    if not database.get_data_request(request_id):
        raise HTTPException(404, "데이터 요청을 찾을 수 없습니다")
    try:
        return database.set_data_request_status(request_id, body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/data-requests/{request_id}/to-task")
def api_data_request_to_task(request_id: int, _: dict = Depends(require_admin)):
    """데이터 요청을 '데이터 연결' 업무 카드로 만든다."""
    dr = database.get_data_request(request_id)
    if not dr:
        raise HTTPException(404, "데이터 요청을 찾을 수 없습니다")
    priority = "high" if dr.get("blocking") else "medium"
    return pipeline.data_request_to_task(dr, priority=priority)


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


STATUS_LABEL = {
    "suggested": "AI 제안", "todo": "할 일", "in_progress": "진행 중", "done": "완료",
}


@app.post("/api/tasks")
def api_create_task(body: TaskCreate, user: dict = Depends(require_user)):
    if body.status not in database.VALID_STATUSES:
        raise HTTPException(400, f"잘못된 상태값: {body.status}")
    task = database.create_task(**body.model_dump(), source="manual")
    database.log_activity(
        _today(), "task_created", task_id=task["id"], task_title=task["title"],
        user_name=user["name"],
    )
    return task


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: int, body: TaskUpdate, user: dict = Depends(require_user)):
    before = database.get_task(task_id)
    if before is None:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    try:
        task = database.update_task(task_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 활동 기록 (보고 자동화의 원천)
    if task["status"] != before["status"]:
        database.log_activity(
            _today(), "status_changed", task_id=task_id, task_title=task["title"],
            user_name=user["name"],
            detail=f"{STATUS_LABEL.get(before['status'])} → {STATUS_LABEL.get(task['status'])}",
        )
    if task["assignee"] != before["assignee"] and task["assignee"]:
        database.log_activity(
            _today(), "assigned", task_id=task_id, task_title=task["title"],
            user_name=user["name"], detail=f"담당: {task['assignee']}",
        )
    # 암묵적 학습 신호: AI 제안을 실제 업무로 수락하면 긍정 피드백
    if (
        before["source"] == "ai"
        and before["status"] == "suggested"
        and task["status"] in ("todo", "in_progress")
    ):
        database.add_feedback(
            kind="task_accepted",
            signal="positive",
            ref_id=task_id,
            context=before["title"],
            user_name=user["name"],
            user_id=user["id"],
        )
    return task


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int, user: dict = Depends(require_user), reason: str = ""):
    task = database.get_task(task_id)
    if task is None:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    database.delete_task(task_id)
    database.log_activity(
        _today(), "task_deleted", task_id=task_id, task_title=task["title"],
        user_name=user["name"], detail=reason.strip(),
    )
    # 암묵적 학습 신호: AI 제안을 채택 없이 삭제하면 부정 피드백
    if task["source"] == "ai" and task["status"] == "suggested":
        database.add_feedback(
            kind="task_rejected",
            signal="negative",
            ref_id=task_id,
            comment=reason.strip(),
            context=task["title"],
            user_name=user["name"],
            user_id=user["id"],
        )
    return {"ok": True}


@app.post("/api/tasks/{task_id}/delegate")
def api_delegate_task(task_id: int, user: dict = Depends(require_user)):
    """AI가 이 업무를 직접 수행해 결과물을 카드에 첨부한다."""
    task = database.get_task(task_id)
    if task is None:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    if task["status"] == "done":
        raise HTTPException(400, "이미 완료된 업무입니다")
    try:
        product = worker.do_task(task, user["name"])
    except Exception as e:
        logger.exception("AI 업무 수행 실패")
        raise HTTPException(500, f"AI 업무 수행 실패: {e}")
    database.log_activity(
        _today(), "deliverable", task_id=task_id, task_title=task["title"],
        user_name=user["name"], detail=product.summary,
    )
    return database.set_task_deliverable(task_id, product.deliverable, product.summary)


# ── 진행 메모 / 막힘 ────────────────────────────────────

class CommentBody(BaseModel):
    text: str
    is_blocker: bool = False


@app.get("/api/tasks/{task_id}/comments")
def api_list_comments(task_id: int, _: dict = Depends(require_user)):
    if not database.get_task(task_id):
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    return database.list_task_comments(task_id)


@app.post("/api/tasks/{task_id}/comments")
def api_add_comment(task_id: int, body: CommentBody, user: dict = Depends(require_user)):
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    if not body.text.strip():
        raise HTTPException(400, "메모 내용을 입력하세요")
    comment = database.add_task_comment(
        task_id, user["name"], body.text.strip(), is_blocker=body.is_blocker
    )
    database.log_activity(
        _today(), "blocker" if body.is_blocker else "comment",
        task_id=task_id, task_title=task["title"],
        user_name=user["name"], detail=body.text.strip(),
    )
    return comment


@app.post("/api/tasks/{task_id}/unblock")
def api_unblock_task(task_id: int, user: dict = Depends(require_user)):
    task = database.get_task(task_id)
    if not task:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    updated = database.set_task_blocked(task_id, False)
    database.log_activity(
        _today(), "unblocked", task_id=task_id, task_title=task["title"],
        user_name=user["name"],
    )
    return updated


# ── 업무 보고 ───────────────────────────────────────────

def require_leader_or_admin(user: dict = Depends(require_user)) -> dict:
    if user["role"] not in ("admin", "leader"):
        raise HTTPException(403, "팀장 또는 관리자 권한이 필요합니다")
    return user


@app.get("/api/work-reports/latest")
def api_latest_work_report(_: dict = Depends(require_user)):
    return database.get_latest_work_report() or {}


@app.post("/api/work-reports/run")
def api_run_work_report(_: dict = Depends(require_leader_or_admin)):
    try:
        return reporter.run_and_save(_today())
    except Exception as e:
        logger.exception("업무 보고 생성 실패")
        raise HTTPException(500, f"업무 보고 생성 실패: {e}")


@app.get("/api/reports/latest")
def api_latest_report(_: dict = Depends(require_user)):
    report = database.get_latest_report()
    return report or {}


@app.get("/api/reviews/latest")
def api_latest_review(_: dict = Depends(require_user)):
    return database.get_latest_review() or {}


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

"""칸반보드 웹 서버 + 매일 아침 자동 분석 스케줄러.

실행: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, database, pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

scheduler = BackgroundScheduler(timezone=config.TIMEZONE)


def _scheduled_run():
    try:
        result = pipeline.run_daily_pipeline()
        logger.info("자동 분석 완료: %s", result["summary"][:100])
    except Exception:
        logger.exception("자동 분석 실패")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
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


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/tasks")
def api_list_tasks():
    return database.list_tasks()


@app.post("/api/tasks")
def api_create_task(body: TaskCreate):
    if body.status not in database.VALID_STATUSES:
        raise HTTPException(400, f"잘못된 상태값: {body.status}")
    return database.create_task(**body.model_dump(), source="manual")


@app.patch("/api/tasks/{task_id}")
def api_update_task(task_id: int, body: TaskUpdate):
    try:
        task = database.update_task(task_id, body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if task is None:
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    return task


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: int):
    if not database.delete_task(task_id):
        raise HTTPException(404, "업무를 찾을 수 없습니다")
    return {"ok": True}


@app.get("/api/reports/latest")
def api_latest_report():
    report = database.get_latest_report()
    return report or {}


@app.get("/api/reports")
def api_list_reports():
    return database.list_reports()


@app.post("/api/run")
def api_run_now(x_run_token: str | None = Header(default=None)):
    """분석 파이프라인 즉시 실행. RUN_TOKEN이 설정돼 있으면 헤더로 인증."""
    if config.RUN_TOKEN and not (
        x_run_token and secrets.compare_digest(x_run_token, config.RUN_TOKEN)
    ):
        raise HTTPException(401, "X-Run-Token 헤더가 올바르지 않습니다")
    try:
        return pipeline.run_daily_pipeline()
    except Exception as e:
        logger.exception("수동 분석 실패")
        raise HTTPException(500, f"분석 실패: {e}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

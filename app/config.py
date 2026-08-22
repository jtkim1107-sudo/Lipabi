"""환경 변수 기반 설정."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

DB_PATH = os.getenv("DB_PATH", "data/lipabi.db")
QUERIES_PATH = os.getenv("QUERIES_PATH", str(BASE_DIR / "config" / "queries.json"))

ANALYSIS_MODEL = os.getenv("ANALYSIS_MODEL", "claude-opus-5")

POWERBI_TENANT_ID = os.getenv("POWERBI_TENANT_ID", "")
POWERBI_CLIENT_ID = os.getenv("POWERBI_CLIENT_ID", "")
POWERBI_CLIENT_SECRET = os.getenv("POWERBI_CLIENT_SECRET", "")
POWERBI_MOCK = os.getenv("POWERBI_MOCK", "false").lower() in ("1", "true", "yes")

DAILY_RUN_TIME = os.getenv("DAILY_RUN_TIME", "07:00")
TIMEZONE = os.getenv("TIMEZONE", "Asia/Seoul")

RUN_TOKEN = os.getenv("RUN_TOKEN", "")

# ── 로그인/보안 ──────────────────────────────────────────
# 최초 실행 시 자동 생성되는 관리자 계정
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_NAME = os.getenv("ADMIN_NAME", "관리자")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")  # 비우면 무작위 생성 후 로그에 출력

# 로그인 유지 기간 (일)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))
# HTTPS로 서비스할 때 true (쿠키에 Secure 플래그)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

# AI에 전달할 쿼리당 최대 행 수 (토큰 비용 관리)
MAX_ROWS_PER_QUERY = int(os.getenv("MAX_ROWS_PER_QUERY", "200"))

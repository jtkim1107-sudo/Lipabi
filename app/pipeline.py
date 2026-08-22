"""매일 아침 파이프라인: Power BI 수집 → AI 분석 → 칸반보드 업무 등록."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import analyzer, briefer, config, database, powerbi

logger = logging.getLogger(__name__)

ROLE_LABEL = {"admin": "관리자", "leader": "팀장", "member": "직원"}


def _build_roster() -> str:
    lines = []
    for u in database.list_users():
        role = ROLE_LABEL.get(u["role"], u["role"])
        team = f", {u['team']}팀" if u.get("team") else ""
        lines.append(f"- {u['name']} ({role}{team})")
    return "\n".join(lines)


def run_daily_pipeline() -> dict:
    """전체 파이프라인을 실행하고 결과 요약을 반환한다."""
    database.init_db()
    run_date = datetime.now(ZoneInfo(config.TIMEZONE)).strftime("%Y-%m-%d")
    logger.info("[%s] 일일 분석 파이프라인 시작", run_date)

    query_results, errors = powerbi.fetch_all()
    if not query_results:
        raise RuntimeError(
            "수집된 데이터가 없습니다. config/queries.json 설정과 Power BI 인증 정보를 확인하세요. "
            + "; ".join(errors)
        )

    profile = database.get_agent_profile()
    pending = [r["title"] for r in database.list_data_requests(statuses=("open", "tasked"))]
    result = analyzer.analyze(
        query_results, run_date, profile, roster=_build_roster(), pending_requests=pending
    )

    data_notes = "; ".join(errors)
    report_id = database.create_report(
        run_date=run_date,
        summary=result.summary,
        insights=result.insights,
        data_notes=data_notes,
    )

    created = []
    for t in result.tasks:
        task = database.create_task(
            title=t.title,
            description=t.description,
            priority=t.priority,
            category=t.category,
            status="suggested",
            source="ai",
            report_id=report_id,
            suggested_assignee=t.suggested_assignee or "",
        )
        created.append(task)

    # 분석가의 데이터 요청 저장 (열려 있는 동일 요청은 중복 생성 안 함)
    requests_created = 0
    for dr in result.data_requests:
        if database.find_active_request_by_title(dr.title):
            continue
        database.create_data_request(
            title=dr.title,
            reason=dr.reason,
            suggested_dax=dr.suggested_dax or "",
            report_id=report_id,
        )
        requests_created += 1

    # 아침 자동화: 분석 직후 전 직원 브리핑을 미리 생성해 둔다
    briefings_created = 0
    if config.AUTO_BRIEFINGS:
        for u in database.list_users():
            try:
                briefer.get_or_create(u, run_date, refresh=True)
                briefings_created += 1
            except Exception:
                logger.exception("브리핑 자동 생성 실패 (%s)", u["name"])

    logger.info(
        "리포트 #%d 저장, 업무 %d건 등록, 브리핑 %d건 생성",
        report_id, len(created), briefings_created,
    )
    return {
        "report_id": report_id,
        "run_date": run_date,
        "summary": result.summary,
        "insights": result.insights,
        "tasks_created": len(created),
        "briefings_created": briefings_created,
        "data_requests_created": requests_created,
        "data_errors": errors,
    }

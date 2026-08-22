"""매일 아침 파이프라인: Power BI 수집 → AI 분석 → 칸반보드 업무 등록."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import analyzer, config, database, powerbi

logger = logging.getLogger(__name__)


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
    result = analyzer.analyze(query_results, run_date, profile)

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
        )
        created.append(task)

    logger.info("리포트 #%d 저장, 업무 %d건 등록", report_id, len(created))
    return {
        "report_id": report_id,
        "run_date": run_date,
        "summary": result.summary,
        "insights": result.insights,
        "tasks_created": len(created),
        "data_errors": errors,
    }

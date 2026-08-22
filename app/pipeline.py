"""매일 아침 파이프라인: Power BI 수집 → AI 분석 → 칸반보드 업무 등록."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import json

from . import analyzer, briefer, config, database, powerbi, reviewer

logger = logging.getLogger(__name__)

ROLE_LABEL = {"admin": "관리자", "leader": "팀장", "member": "직원"}


def _build_roster() -> str:
    lines = []
    for u in database.list_users():
        role = ROLE_LABEL.get(u["role"], u["role"])
        team = f", {u['team']}팀" if u.get("team") else ""
        lines.append(f"- {u['name']} ({role}{team})")
    return "\n".join(lines)


def data_request_to_task(dr: dict, priority: str = "medium") -> dict:
    """데이터 요청을 '데이터 연결' 업무 카드로 만들고 요청을 tasked로 표시한다."""
    description = f"분석가 요청 사유: {dr['reason']}"
    if dr.get("suggested_dax"):
        description += (
            "\n\n제안 DAX 쿼리 (config/queries.json에 추가):\n" + dr["suggested_dax"]
        )
    task = database.create_task(
        title=f"데이터 연결: {dr['title']}",
        description=description,
        priority=priority,
        category="데이터",
        status="todo",
        source="manual",
        report_id=dr.get("report_id"),
    )
    database.set_data_request_status(dr["id"], "tasked")
    return task


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

    # 자기 채점: 어제 리포트를 오늘 데이터로 검증 (오늘 리포트 생성 전에 수행)
    self_review_text = ""
    review_stats = None
    prev_report = database.get_latest_report()
    if config.SELF_REVIEW and prev_report and prev_report["run_date"] != run_date:
        try:
            sr = reviewer.review(prev_report, query_results, run_date)
            database.save_review(
                prev_report["id"], run_date,
                json.dumps(sr.model_dump(), ensure_ascii=False),
            )
            # 틀린 데서 나온 교훈은 피드백으로 축적 → 주간 학습에 자동 반영
            for lesson in sr.lessons:
                database.add_feedback(
                    kind="self_review",
                    signal="negative",
                    ref_id=prev_report["id"],
                    comment=lesson,
                    context="자기 채점",
                    user_name="분석가(자동)",
                )
            self_review_text = reviewer.to_prompt_text(sr)
            review_stats = {
                "graded": len(sr.verdicts),
                "wrong": sum(1 for v in sr.verdicts if v.verdict == "wrong"),
                "lessons": len(sr.lessons),
            }
        except Exception:
            logger.exception("자기 채점 실패 — 오늘 분석은 계속 진행")

    profile = database.get_agent_profile()
    pending = [r["title"] for r in database.list_data_requests(statuses=("open", "tasked"))]
    result = analyzer.analyze(
        query_results, run_date, profile, roster=_build_roster(),
        pending_requests=pending, self_review=self_review_text,
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
    # blocking 요청은 관리자 확인을 기다리지 않고 즉시 높은 우선순위 업무로 만든다
    requests_created = 0
    blocking_tasks = 0
    for dr in result.data_requests:
        if database.find_active_request_by_title(dr.title):
            continue
        row = database.create_data_request(
            title=dr.title,
            reason=dr.reason,
            suggested_dax=dr.suggested_dax or "",
            report_id=report_id,
            blocking=dr.blocking,
        )
        requests_created += 1
        if dr.blocking:
            data_request_to_task(row, priority="high")
            blocking_tasks += 1

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
        "blocking_tasks_created": blocking_tasks,
        "self_review": review_stats,
        "data_errors": errors,
    }

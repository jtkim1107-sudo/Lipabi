"""주간 개선 회고: 한 주의 리포트·문제 추적·업무 흐름·자기 채점을 돌아보고
회사가 나아지고 있는지, 다음 주에 무엇을 고쳐야 하는지 정리한다.
"""
import json
import logging
from datetime import date, timedelta
from typing import List

import anthropic
from pydantic import BaseModel, Field

from . import config, database

logger = logging.getLogger(__name__)


class WeeklyRetro(BaseModel):
    summary: str = Field(description="이번 주 총평 (2~4문장) — 회사가 나아졌는가, 무엇 때문인가")
    improved: List[str] = Field(description="이번 주 실제로 나아진 것 (데이터 근거 포함)")
    worsened: List[str] = Field(description="나빠졌거나 해결되지 않은 것 (숨기지 말 것)")
    recurring: List[str] = Field(
        default_factory=list,
        description="반복해서 나타나는 문제·패턴 — 구조적으로 손봐야 할 것",
    )
    priorities: List[str] = Field(description="다음 주 개선 우선순위 3~5개 (담당 팀 명시)")


SYSTEM_PROMPT = """당신은 회사의 개선 회고 진행자입니다. 한 주간의 아침 리포트, \
문제 추적 기록, 업무 흐름, 분석가 자기 채점을 검토해 주간 회고를 작성합니다.

원칙:
- 목적은 칭찬도 질책도 아니라 "다음 주에 무엇을 고칠 것인가"입니다.
- 나아진 것과 나빠진 것을 모두 데이터 근거와 함께 적습니다. 나빠진 것을 숨기지 않습니다.
- 같은 문제가 반복되면 개별 대응이 아니라 구조적 개선(프로세스·데이터·담당)을 제안합니다.
- 다음 주 우선순위는 실행 가능하게, 담당 팀을 명시해 적습니다.
- 모든 출력은 한국어로 작성합니다."""

ISSUE_STATUS_LABEL = {
    "open": "신규", "improving": "개선 중", "worsening": "악화 중",
    "stalled": "정체", "resolved": "해결",
}


def generate(run_date: str) -> WeeklyRetro | None:
    """최근 7일을 돌아보는 회고를 생성한다. 리포트가 없으면 None."""
    week_start = (date.fromisoformat(run_date) - timedelta(days=6)).isoformat()
    reports = database.list_reports_since(week_start)
    if not reports:
        return None

    report_lines = [f"- [{r['run_date']}] {r['summary']}" for r in reports]

    issue_lines = []
    for i in database.list_issues(include_resolved=True):
        logs = [l for l in database.list_issue_logs(i["id"]) if l["day"] >= week_start]
        if not logs and i["status"] == "resolved" and (i["resolved_at"] or "") < week_start:
            continue  # 이번 주와 무관한 과거 해결 건 제외
        history = " → ".join(
            f"{l['day'][5:]} {ISSUE_STATUS_LABEL.get(l['status'], l['status'])}"
            for l in reversed(logs)
        )
        issue_lines.append(
            f"- {i['title']} (심각도 {i['severity']}, 현재 {ISSUE_STATUS_LABEL.get(i['status'])})"
            + (f" — 이번 주: {history}" if history else "")
        )

    created = done = blockers = 0
    d = date.fromisoformat(week_start)
    while d <= date.fromisoformat(run_date):
        for a in database.list_activities(d.isoformat()):
            if a["kind"] == "task_created":
                created += 1
            elif a["kind"] == "status_changed" and "완료" in a["detail"]:
                done += 1
            elif a["kind"] == "blocker":
                blockers += 1
        d += timedelta(days=1)

    graded = wrong = 0
    for rv in database.list_reviews_since(week_start):
        verdicts = rv["content"].get("verdicts", [])
        graded += len(verdicts)
        wrong += sum(1 for v in verdicts if v.get("verdict") == "wrong")

    user_message = (
        f"회고 기준일: {run_date} (지난 7일: {week_start} ~ {run_date})\n\n"
        f"## 이번 주 아침 리포트 요약\n" + "\n".join(report_lines)
        + "\n\n## 문제 추적 현황\n" + ("\n".join(issue_lines) or "(추적 중인 문제 없음)")
        + f"\n\n## 업무 흐름\n- 생성 {created}건 / 완료 {done}건 / 막힘 표시 {blockers}건"
        + f"\n\n## 분석가 자기 채점\n- 인사이트 {graded}개 채점, 틀림 {wrong}개"
        + "\n\n이번 주 회고를 작성해 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=WeeklyRetro,
    )
    logger.info("주간 회고 생성: 리포트 %d건, 우선순위 %d개",
                len(reports), len(response.parsed_output.priorities))
    return response.parsed_output


def run_and_save(run_date: str) -> dict:
    retro = generate(run_date)
    if retro is None:
        return {"generated": False, "message": "이번 주 리포트가 없어 회고를 건너뜁니다"}
    content = retro.model_dump()
    content["week_of"] = run_date
    database.save_retro(run_date, json.dumps(content, ensure_ascii=False))
    return {"generated": True, **content}

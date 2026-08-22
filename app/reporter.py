"""저녁 업무 보고 자동 생성: 하루 동안 보드에서 일어난 활동을 모아
경영진용 일일 업무 보고를 만든다.

직원이 보고서를 쓰지 않아도 — 카드 이동, 완료, 메모, 막힘 표시가 곧 보고가 된다.
"""
import json
import logging
from typing import List

import anthropic
from pydantic import BaseModel, Field

from . import config, database

logger = logging.getLogger(__name__)


class PersonReport(BaseModel):
    name: str = Field(description="직원 이름")
    items: List[str] = Field(description="이 사람의 오늘 활동 요약 (완료/진행/메모 기반, 1~4개)")


class WorkReport(BaseModel):
    summary: str = Field(description="오늘 조직 전체 업무 현황 요약 (2~4문장)")
    highlights: List[str] = Field(description="오늘의 주요 완료·진전 사항")
    blockers: List[str] = Field(
        description="막힘/지연/도움 필요 — 경영진이 조치해야 할 것 (없으면 빈 목록, 숨기지 말 것)"
    )
    by_person: List[PersonReport] = Field(description="사람별 현황 (활동이 있었던 사람만)")


SYSTEM_PROMPT = """당신은 조직의 보고 담당자입니다. 오늘 하루 팀 칸반보드에서 일어난 \
활동 기록을 바탕으로 경영진용 일일 업무 보고를 작성합니다.

원칙:
- 기록에 있는 사실만 씁니다. 부풀리거나 추측하지 않습니다.
- 막힘(blocker)과 지연은 절대 숨기지 않고 blockers에 그대로 올립니다.
  보고의 목적은 잘한 척이 아니라 경영진이 제때 조치하게 하는 것입니다.
- 활동이 없었던 사람을 지적하지 않습니다. 있었던 활동만 정리합니다.
- 카드가 오래 진행 중에 머물러 있으면 지연 가능성으로 짚어 줍니다.
- 간결하게: 항목당 한 문장.
- 모든 출력은 한국어로 작성합니다."""

KIND_LABEL = {
    "task_created": "업무 생성",
    "status_changed": "상태 변경",
    "assigned": "담당 지정",
    "task_deleted": "업무 삭제",
    "deliverable": "AI 결과물 작성",
    "comment": "진행 메모",
    "blocker": "막힘 표시",
    "unblocked": "막힘 해제",
}


def generate(run_date: str) -> WorkReport | None:
    """오늘 활동으로 업무 보고를 생성한다. 활동이 없으면 None."""
    activities = database.list_activities(run_date)
    if not activities:
        return None

    act_lines = []
    for a in activities:
        kind = KIND_LABEL.get(a["kind"], a["kind"])
        line = f"- [{kind}] {a['task_title']}"
        if a["detail"]:
            line += f" — {a['detail']}"
        if a["user_name"]:
            line += f" (by {a['user_name']})"
        act_lines.append(line)

    tasks = database.list_tasks()
    board_lines = []
    for t in tasks:
        if t["status"] in ("todo", "in_progress") or (t["status"] == "done"):
            flag = " [막힘]" if t.get("blocked") else ""
            board_lines.append(
                f"- ({t['status']}) {t['title']} / 담당: {t['assignee'] or '미배정'} / 우선순위: {t['priority']}{flag}"
            )

    user_message = (
        f"보고 날짜: {run_date}\n\n"
        f"## 오늘의 활동 기록 (시간순)\n" + "\n".join(act_lines)
        + "\n\n## 현재 보드 상태\n" + "\n".join(board_lines)
        + "\n\n오늘의 일일 업무 보고를 작성해 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=WorkReport,
    )
    result = response.parsed_output
    logger.info("업무 보고 생성: 활동 %d건, 막힘 %d건", len(activities), len(result.blockers))
    return result


def run_and_save(run_date: str) -> dict:
    report = generate(run_date)
    if report is None:
        return {"generated": False, "message": "오늘 기록된 활동이 없습니다"}
    content = report.model_dump()
    content["run_date"] = run_date
    database.save_work_report(run_date, json.dumps(content, ensure_ascii=False))
    return {"generated": True, **content}

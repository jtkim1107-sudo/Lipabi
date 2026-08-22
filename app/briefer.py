"""개인 브리핑: 각 직원의 에이전트가 아침 분석과 보드 현황을 보고
'오늘 이 사람이 집중할 일'을 개인화해서 정리한다."""
import json
import logging
from typing import List, Optional

import anthropic
from pydantic import BaseModel, Field

from . import config, database

logger = logging.getLogger(__name__)


class FocusItem(BaseModel):
    title: str = Field(description="업무 제목")
    reason: str = Field(description="왜 오늘 이 업무에 집중해야 하는지 (한두 문장)")
    task_id: Optional[int] = Field(default=None, description="보드에 있는 업무면 해당 id, 아니면 null")


class Briefing(BaseModel):
    headline: str = Field(description="한 줄 인사 + 오늘 상황 요약 (1~2문장)")
    focus: List[FocusItem] = Field(description="오늘 집중할 업무, 중요한 순서대로 3~5개")
    tip: str = Field(description="담당자의 스타일에 맞춘 조언 한 마디")


SYSTEM_PROMPT_TEMPLATE = """당신은 '{agent_name}'라는 이름의 개인 업무 비서이며, \
담당자는 {user_name}님입니다. 매일 아침 회사의 데이터 분석 결과와 팀 칸반보드를 검토하고, \
담당자가 오늘 무엇에 집중해야 할지 개인 브리핑을 만듭니다.

원칙:
- 담당자의 지침과 학습된 선호를 최우선으로 반영합니다.
- 담당자에게 이미 배정된 업무와, 담당자의 역할에 맞는 미배정 업무를 중심으로 제안합니다.
- 보드에 실제로 있는 업무는 반드시 task_id를 포함합니다.
- 근거 없는 추측은 하지 않고, 분석 결과와 보드에 있는 정보만 사용합니다.
- 모든 출력은 한국어로 작성합니다.

## 담당자의 지침
{instructions}

## 담당자에 대해 학습한 내용
{lessons}"""


def generate(user: dict, run_date: str) -> Briefing:
    agent = database.get_user_agent(user["id"], default_name=f"{user['name']}의 에이전트")
    report = database.get_latest_report()
    tasks = database.list_tasks()

    # 관련 업무만 추림: AI 제안 전체 + 진행 중이지 않은 미배정 할 일 + 본인 배정 업무
    relevant = [
        t
        for t in tasks
        if t["status"] == "suggested"
        or (t["status"] in ("todo", "in_progress") and t["assignee"] in ("", user["name"]))
    ]
    task_lines = [
        {
            "task_id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "priority": t["priority"],
            "category": t["category"],
            "status": t["status"],
            "assignee": t["assignee"] or "(미배정)",
        }
        for t in relevant
    ]

    report_text = "(오늘 분석 리포트가 아직 없습니다)"
    if report:
        report_text = (
            f"[{report['run_date']}] {report['summary']}\n인사이트:\n"
            + "\n".join(f"- {i}" for i in report["insights"])
        )

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=agent["name"],
        user_name=user["name"],
        instructions=agent["instructions"].strip() or "(아직 작성되지 않음)",
        lessons=agent["lessons"].strip() or "(아직 없음)",
    )
    user_message = (
        f"오늘 날짜: {run_date}\n\n"
        f"## 오늘의 AI 분석\n{report_text}\n\n"
        f"## 칸반보드 현황 (관련 업무)\n"
        f"```json\n{json.dumps(task_lines, ensure_ascii=False)}\n```\n\n"
        f"{user['name']}님을 위한 오늘의 브리핑을 만들어 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        output_format=Briefing,
    )
    logger.info("브리핑 생성 완료 (%s): 집중 업무 %d개", user["name"], len(response.parsed_output.focus))
    return response.parsed_output


def get_or_create(user: dict, run_date: str, refresh: bool = False) -> dict:
    """오늘 브리핑을 반환한다 (캐시 우선, refresh 시 재생성)."""
    if not refresh:
        cached = database.get_briefing(user["id"], run_date)
        if cached:
            return json.loads(cached)
    briefing = generate(user, run_date).model_dump()
    briefing["run_date"] = run_date
    database.save_briefing(user["id"], run_date, json.dumps(briefing, ensure_ascii=False))
    return briefing

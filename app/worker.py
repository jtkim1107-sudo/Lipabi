"""AI 실무 수행: 칸반보드의 업무를 AI가 직접 수행해 결과물 초안을 만든다.

보고서 초안, 이메일 초안, 점검 체크리스트, 데이터 분석 요약 등
'문서로 완결되는 업무'를 대신 수행한다. 결과물은 카드에 첨부되고
직원은 검토·수정 후 사용한다.
"""
import logging

import anthropic
from pydantic import BaseModel, Field

from . import config, database

logger = logging.getLogger(__name__)


class WorkProduct(BaseModel):
    summary: str = Field(description="무엇을 만들었는지 한 줄 요약 (예: '경영진 공유용 주간 매출 보고서 초안')")
    deliverable: str = Field(
        description="실제 결과물 전문 (마크다운). 바로 사용할 수 있는 완성본 수준으로 작성"
    )


SYSTEM_PROMPT = """당신은 회사의 AI 실무자입니다. 칸반보드에 등록된 업무를 담당자를 대신해 \
직접 수행하고, 바로 사용할 수 있는 결과물을 만듭니다.

원칙:
- 업무 내용에 맞는 결과물 형태를 스스로 판단합니다:
  보고서/이메일 초안/체크리스트/분석 요약/기획 초안 등.
- 회사의 최신 분석 데이터가 주어지면 그 수치를 정확히 인용해 근거로 씁니다.
- 확인이 필요한 부분(외부 시스템 조회, 담당자 결정 등)은 결과물 안에
  `[확인 필요: ...]` 표시로 명시하고 나머지는 완성합니다.
- 결과물은 검토 후 바로 쓸 수 있도록 실무 문서 수준으로 작성합니다.
- 모든 출력은 한국어로 작성합니다."""


def do_task(task: dict, requester_name: str) -> WorkProduct:
    """업무 하나를 수행해 결과물을 반환한다."""
    profile = database.get_agent_profile()
    report = database.get_latest_report()

    context_parts = []
    if profile.get("instructions", "").strip():
        context_parts.append("## 회사 운영 지침\n" + profile["instructions"].strip())
    if profile.get("lessons", "").strip():
        context_parts.append("## 회사가 선호하는 방식 (학습됨)\n" + profile["lessons"].strip())
    if report:
        context_parts.append(
            f"## 최신 분석 데이터 ({report['run_date']})\n{report['summary']}\n"
            + "\n".join(f"- {i}" for i in report["insights"])
        )

    user_message = (
        ("\n\n".join(context_parts) + "\n\n" if context_parts else "")
        + "## 수행할 업무\n"
        + f"- 제목: {task['title']}\n"
        + (f"- 상세: {task['description']}\n" if task["description"] else "")
        + (f"- 분류: {task['category']}\n" if task["category"] else "")
        + f"- 우선순위: {task['priority']}\n"
        + f"- 요청자: {requester_name}\n\n"
        + "이 업무를 지금 수행해서 결과물을 만들어 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=WorkProduct,
    )
    logger.info("AI 업무 수행 완료: '%s' (요청: %s)", task["title"], requester_name)
    return response.parsed_output

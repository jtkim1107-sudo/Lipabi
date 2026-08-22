"""Claude API로 Power BI 데이터를 분석해 요약·인사이트·업무 제안을 생성한다."""
import json
import logging
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

from . import config

logger = logging.getLogger(__name__)


class SuggestedTask(BaseModel):
    title: str = Field(description="업무 제목 (간결한 한 줄)")
    description: str = Field(description="무엇을, 왜, 어떻게 해야 하는지 구체적 설명")
    priority: Literal["high", "medium", "low"]
    category: str = Field(description="업무 분류 (예: 영업, 재고, 마케팅, 데이터 점검)")


class AnalysisResult(BaseModel):
    summary: str = Field(description="오늘 데이터의 핵심 요약 (2~4문장)")
    insights: List[str] = Field(description="주목할 만한 발견/이상 징후/트렌드 목록")
    tasks: List[SuggestedTask] = Field(description="오늘 팀이 실행해야 할 업무 제안 (3~7개)")


SYSTEM_PROMPT = """당신은 회사의 데이터 분석가입니다. 매일 아침 Power BI에서 수집된 \
데이터를 검토하고, 경영진과 실무 팀이 바로 실행할 수 있는 업무를 제안합니다.

원칙:
- 데이터에 실제로 근거한 내용만 말합니다. 수치를 인용할 때는 정확하게 인용합니다.
- 인사이트는 "그래서 무엇을 해야 하는가"로 이어지도록 작성합니다.
- 업무 제안은 담당자가 읽고 바로 착수할 수 있을 만큼 구체적으로 작성합니다.
- 급격한 하락, 이상치, 전일/전주 대비 변화에 특히 주목합니다.
- 모든 출력은 한국어로 작성합니다."""


def analyze(query_results: list[dict], run_date: str) -> AnalysisResult:
    """수집된 데이터를 Claude에 보내 구조화된 분석 결과를 받는다."""
    sections = []
    for r in query_results:
        rows_json = json.dumps(r["rows"], ensure_ascii=False, default=str)
        sections.append(
            f"### {r['name']}\n{r['description']}\n```json\n{rows_json}\n```"
        )
    user_message = (
        f"오늘 날짜: {run_date}\n\n"
        f"아래는 오늘 아침 Power BI에서 수집한 데이터입니다.\n\n"
        + "\n\n".join(sections)
        + "\n\n이 데이터를 분석해 요약, 인사이트, 그리고 오늘 팀이 실행할 업무 목록을 만들어 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=AnalysisResult,
    )
    result = response.parsed_output
    logger.info("분석 완료: 인사이트 %d개, 업무 제안 %d개", len(result.insights), len(result.tasks))
    return result

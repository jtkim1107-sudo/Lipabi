"""Claude API로 Power BI 데이터를 분석해 요약·인사이트·업무 제안을 생성한다."""
import json
import logging
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from . import config

logger = logging.getLogger(__name__)


class SuggestedTask(BaseModel):
    title: str = Field(description="업무 제목 (간결한 한 줄)")
    description: str = Field(description="무엇을, 왜, 어떻게 해야 하는지 구체적 설명")
    priority: Literal["high", "medium", "low"]
    category: str = Field(description="업무 분류 (예: 영업, 재고, 마케팅, 데이터 점검)")
    suggested_assignee: Optional[str] = Field(
        default=None,
        description="팀 구성원 명단에서 이 업무에 가장 적합한 사람 이름 (명단이 없거나 판단이 어려우면 null)",
    )


class DataRequestItem(BaseModel):
    title: str = Field(description="필요한 데이터 한 줄 (예: '제품별 재고 수량과 입고 예정일')")
    reason: str = Field(description="이 데이터가 있으면 어떤 분석/판단이 가능해지는지")
    suggested_dax: Optional[str] = Field(
        default=None,
        description="Power BI에 있을 법한 데이터면 EVALUATE로 시작하는 예시 DAX 쿼리, 외부 데이터면 null",
    )
    blocking: bool = Field(
        default=False,
        description="이 데이터가 없어서 오늘의 핵심 질문(급락 원인, 이상 징후 원인 등)에 답할 수 없으면 true — 단순히 있으면 좋은 정도면 false",
    )


class AnalysisResult(BaseModel):
    summary: str = Field(description="오늘 데이터의 핵심 요약 (2~4문장)")
    insights: List[str] = Field(description="주목할 만한 발견/이상 징후/트렌드 목록")
    tasks: List[SuggestedTask] = Field(description="오늘 팀이 실행해야 할 업무 제안 (3~7개)")
    data_requests: List[DataRequestItem] = Field(
        default_factory=list,
        description="분석에 꼭 필요한데 지금 없는 데이터 요청 (0~3개, 정말 필요한 것만)",
    )


BASE_SYSTEM_PROMPT = """당신은 회사의 데이터 분석가입니다. 매일 아침 Power BI에서 수집된 \
데이터를 검토하고, 경영진과 실무 팀이 바로 실행할 수 있는 업무를 제안합니다.

원칙:
- 데이터에 실제로 근거한 내용만 말합니다. 수치를 인용할 때는 정확하게 인용합니다.
- 인사이트는 "그래서 무엇을 해야 하는가"로 이어지도록 작성합니다.
- 업무 제안은 담당자가 읽고 바로 착수할 수 있을 만큼 구체적으로 작성합니다.
- 급격한 하락, 이상치, 전일/전주 대비 변화에 특히 주목합니다.
- 분석에 꼭 필요한 데이터가 없어서 판단이 제한되면, 추측하지 말고 data_requests로
  그 데이터를 요청합니다 (무엇이, 왜 필요한지). 이미 대기 중인 요청은 다시 요청하지 않습니다.
- 데이터가 없어 원인을 확인할 수 없는 인사이트는 "○○ 데이터가 없어 확인 불가"라고
  명시하고, 그 데이터가 오늘의 핵심 질문에 답하는 데 필수라면 blocking=true로 요청합니다.
- 모든 출력은 한국어로 작성합니다."""


def build_system_prompt(profile: dict | None) -> str:
    """기본 프롬프트에 운영자의 지침과 학습된 교훈을 결합한다."""
    parts = [BASE_SYSTEM_PROMPT]
    if profile:
        if profile.get("name"):
            parts[0] = f"당신의 이름은 '{profile['name']}'입니다. " + BASE_SYSTEM_PROMPT
        if profile.get("instructions", "").strip():
            parts.append(
                "## 운영자의 지침 (반드시 따르세요)\n"
                "아래는 이 회사 운영자가 직접 작성한 분석 기준입니다. "
                "당신은 운영자의 관점과 판단 기준을 대신하는 분석가입니다.\n\n"
                + profile["instructions"].strip()
            )
        if profile.get("lessons", "").strip():
            parts.append(
                "## 과거 피드백에서 학습한 교훈\n"
                "지금까지 운영자와 팀의 피드백을 통해 정리된 교훈입니다. "
                "분석과 업무 제안에 반영하세요.\n\n"
                + profile["lessons"].strip()
            )
    return "\n\n".join(parts)


def analyze(query_results: list[dict], run_date: str, profile: dict | None = None,
            roster: str = "", pending_requests: list[str] | None = None) -> AnalysisResult:
    """수집된 데이터를 Claude에 보내 구조화된 분석 결과를 받는다."""
    sections = []
    for r in query_results:
        rows_json = json.dumps(r["rows"], ensure_ascii=False, default=str)
        sections.append(
            f"### {r['name']}\n{r['description']}\n```json\n{rows_json}\n```"
        )
    roster_section = ""
    if roster.strip():
        roster_section = (
            "\n\n## 팀 구성원\n"
            + roster.strip()
            + "\n\n업무마다 이 명단에서 가장 적합한 담당자를 suggested_assignee로 추천하세요. "
            "역할과 팀이 맞는 사람이 없으면 null로 두세요."
        )
    pending_section = ""
    if pending_requests:
        pending_section = (
            "\n\n## 이미 요청되어 대기 중인 데이터 (중복 요청 금지)\n"
            + "\n".join(f"- {t}" for t in pending_requests)
        )
    user_message = (
        f"오늘 날짜: {run_date}\n\n"
        f"아래는 오늘 아침 Power BI에서 수집한 데이터입니다.\n\n"
        + "\n\n".join(sections)
        + roster_section
        + pending_section
        + "\n\n이 데이터를 분석해 요약, 인사이트, 그리고 오늘 팀이 실행할 업무 목록을 만들어 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=build_system_prompt(profile),
        messages=[{"role": "user", "content": user_message}],
        output_format=AnalysisResult,
    )
    result = response.parsed_output
    logger.info("분석 완료: 인사이트 %d개, 업무 제안 %d개", len(result.insights), len(result.tasks))
    return result

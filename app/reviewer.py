"""자기 채점: 어제 리포트의 인사이트를 오늘 실제 데이터와 대조해 스스로 평가한다.

- 맞은 것/틀린 것/확인 불가를 가려내고
- 틀린 패턴에서 일반화된 교훈을 뽑아 피드백으로 축적한다 (주간 학습에 반영)
- 채점 결과는 당일 분석 프롬프트에도 주입되어 같은 실수를 반복하지 않게 한다
"""
import json
import logging
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field

from . import config

logger = logging.getLogger(__name__)


class InsightVerdict(BaseModel):
    insight: str = Field(description="채점 대상 인사이트 (어제 문구 그대로)")
    verdict: Literal["correct", "wrong", "unverifiable"] = Field(
        description="오늘 데이터로 검증한 결과: correct(맞음) / wrong(틀림) / unverifiable(오늘 데이터로 확인 불가)"
    )
    evidence: str = Field(description="판정 근거 — 오늘 데이터의 구체적 수치를 인용해 한두 문장")


class SelfReview(BaseModel):
    verdicts: List[InsightVerdict] = Field(description="어제 인사이트별 채점")
    accuracy_note: str = Field(description="총평 1~2문장 (무엇을 잘 봤고 무엇을 놓쳤는지)")
    lessons: List[str] = Field(
        default_factory=list,
        description="틀린 판정에서 뽑은 일반화 교훈 0~3개 (개별 사건이 아니라 다음에도 적용될 원칙으로)",
    )


SYSTEM_PROMPT = """당신은 데이터 분석가의 자기 채점관입니다. 어제 아침 분석에서 내놓은 \
인사이트를 오늘 새로 수집된 실제 데이터와 대조해 엄격하게 채점합니다.

원칙:
- 관대하게 채점하지 않습니다. 오늘 데이터가 어제 주장을 뒷받침하면 correct,
  반대되면 wrong, 오늘 데이터만으로 판단할 수 없으면 unverifiable입니다.
- 판정 근거는 반드시 오늘 데이터의 구체적 수치를 인용합니다.
- 예측이 아닌 단순 사실 서술(예: "어제 매출은 X였다")은 unverifiable이 아니라
  오늘 데이터와 모순되는지만 봅니다.
- 틀린 판정이 있으면, 왜 틀렸는지를 일반화한 교훈을 lessons로 남깁니다.
  (예: "8/21 의류 하락 원인 오판" ❌ → "하락 원인을 단정하기 전에 최소 2개 지표로 교차 확인" ⭕)
- 모든 출력은 한국어로 작성합니다."""


def review(prev_report: dict, query_results: list[dict], run_date: str) -> SelfReview:
    """어제 리포트를 오늘 데이터로 채점한다."""
    sections = []
    for r in query_results:
        rows_json = json.dumps(r["rows"], ensure_ascii=False, default=str)
        sections.append(f"### {r['name']}\n```json\n{rows_json}\n```")

    user_message = (
        f"오늘 날짜: {run_date}\n\n"
        f"## 어제({prev_report['run_date']}) 분석 리포트\n"
        f"요약: {prev_report['summary']}\n"
        f"인사이트:\n"
        + "\n".join(f"- {i}" for i in prev_report["insights"])
        + "\n\n## 오늘 수집된 실제 데이터\n"
        + "\n\n".join(sections)
        + "\n\n어제 인사이트를 채점해 주세요."
    )

    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        output_format=SelfReview,
    )
    result = response.parsed_output
    wrong = sum(1 for v in result.verdicts if v.verdict == "wrong")
    logger.info("자기 채점 완료: %d개 중 틀림 %d개, 교훈 %d개",
                len(result.verdicts), wrong, len(result.lessons))
    return result


def to_prompt_text(sr: SelfReview) -> str:
    """채점 결과를 당일 분석 프롬프트용 텍스트로 변환."""
    mark = {"correct": "✓ 맞음", "wrong": "✗ 틀림", "unverifiable": "─ 확인 불가"}
    lines = [f"- [{mark[v.verdict]}] {v.insight} — {v.evidence}" for v in sr.verdicts]
    text = "\n".join(lines) + f"\n총평: {sr.accuracy_note}"
    if sr.lessons:
        text += "\n교훈:\n" + "\n".join(f"- {l}" for l in sr.lessons)
    return text

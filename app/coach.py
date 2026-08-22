"""에이전트 교육: 쌓인 피드백을 Claude가 '교훈'으로 정리해 프로필에 축적한다.

피드백 출처:
- 명시적: 리포트 👍/👎, 삭제 시 남긴 이유, 코멘트
- 암묵적: AI 제안 카드를 수락(할 일로 이동)하거나 삭제한 행동
"""
import logging

import anthropic
from pydantic import BaseModel, Field

from . import config, database

logger = logging.getLogger(__name__)

SIGNAL_LABEL = {"positive": "긍정", "negative": "부정"}
KIND_LABEL = {
    "report": "아침 리포트",
    "task_accepted": "AI 제안 업무 수락",
    "task_rejected": "AI 제안 업무 삭제",
    "task": "업무",
}


class TrainingResult(BaseModel):
    lessons: str = Field(
        description="갱신된 교훈 목록 (마크다운 불릿, 12개 이내, 각 항목은 구체적이고 실행 가능하게)"
    )
    changelog: str = Field(description="이번 학습에서 무엇이 바뀌었는지 1~3문장 요약")


TRAIN_SYSTEM_PROMPT = """당신은 데이터 분석 에이전트를 교육하는 코치입니다. \
운영자와 팀이 남긴 피드백을 바탕으로, 분석 에이전트가 다음 분석부터 따라야 할 \
'교훈' 목록을 갱신합니다.

규칙:
- 기존 교훈 중 여전히 유효한 것은 유지하고, 새 피드백과 모순되면 수정합니다.
- 개별 사건이 아니라 일반화 가능한 원칙으로 정리합니다.
  (예: "8월 12일 재고 제안 삭제됨" ❌ → "재고 관련 제안은 실제 발주 가능 수량을 확인할 수 있을 때만 제시" ⭕)
- 수락된 제안의 공통점은 강화하고, 삭제된 제안의 공통점은 피하도록 정리합니다.
- 최대 12개 불릿, 각 불릿은 한 문장으로 간결하게.
- 모든 출력은 한국어로 작성합니다."""


PERSONAL_TRAIN_SYSTEM_PROMPT = """당신은 개인 업무 비서 에이전트를 교육하는 코치입니다. \
한 직원이 남긴 피드백과 업무 처리 행동을 바탕으로, 그 직원의 개인 에이전트가 따라야 할 \
'교훈'(그 직원의 선호와 일하는 방식) 목록을 갱신합니다.

규칙:
- 기존 교훈 중 여전히 유효한 것은 유지하고, 새 피드백과 모순되면 수정합니다.
- 개별 사건이 아니라 이 직원의 일반적 선호/업무 스타일로 정리합니다.
  (예: "8월 12일 광고 카드 수락" ❌ → "마케팅/광고 관련 업무를 우선 맡는 편" ⭕)
- 최대 12개 불릿, 각 불릿은 한 문장으로 간결하게.
- 모든 출력은 한국어로 작성합니다."""


def _feedback_lines(feedback: list[dict], include_user: bool) -> str:
    lines = []
    for f in reversed(feedback):  # 시간순
        kind = KIND_LABEL.get(f["kind"], f["kind"])
        signal = SIGNAL_LABEL.get(f["signal"], f["signal"])
        line = f"- [{signal}] {kind}"
        if f["context"]:
            line += f" — 대상: {f['context']}"
        if f["comment"]:
            line += f" — 코멘트: {f['comment']}"
        if include_user and f["user_name"]:
            line += f" (작성: {f['user_name']})"
        lines.append(line)
    return "\n".join(lines)


def _consolidate(system_prompt: str, instructions: str, lessons: str, feedback_text: str) -> TrainingResult:
    user_message = (
        "## 지침\n"
        + (instructions.strip() or "(없음)")
        + "\n\n## 현재 교훈\n"
        + (lessons.strip() or "(아직 없음)")
        + "\n\n## 새로 쌓인 피드백\n"
        + feedback_text
        + "\n\n위 피드백을 반영해 교훈 목록을 갱신해 주세요."
    )
    client = anthropic.Anthropic()
    response = client.messages.parse(
        model=config.ANALYSIS_MODEL,
        max_tokens=16000,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        output_format=TrainingResult,
    )
    return response.parsed_output


def train() -> dict:
    """전사 분석가: 미반영 피드백 전체를 교훈으로 정리하고 프로필을 갱신한다."""
    profile = database.get_agent_profile()
    feedback = database.list_feedback(limit=200, unconsumed_only=True)
    if not feedback:
        return {"trained": False, "message": "새로 반영할 피드백이 없습니다", "lessons": profile["lessons"]}

    result = _consolidate(
        TRAIN_SYSTEM_PROMPT,
        profile["instructions"],
        profile["lessons"],
        _feedback_lines(feedback, include_user=True),
    )
    database.update_agent_profile(lessons=result.lessons)
    database.mark_feedback_consumed([f["id"] for f in feedback])
    logger.info("전사 분석가 학습 완료: 피드백 %d건 반영", len(feedback))
    return {
        "trained": True,
        "feedback_count": len(feedback),
        "lessons": result.lessons,
        "changelog": result.changelog,
    }


def train_personal(user: dict) -> dict:
    """개인 에이전트: 해당 직원의 미반영 피드백을 교훈으로 정리한다."""
    agent = database.get_user_agent(user["id"], default_name=f"{user['name']}의 에이전트")
    feedback = database.list_personal_feedback(user["id"], limit=200, unconsumed_only=True)
    if not feedback:
        return {"trained": False, "message": "새로 반영할 피드백이 없습니다", "lessons": agent["lessons"]}

    result = _consolidate(
        PERSONAL_TRAIN_SYSTEM_PROMPT,
        agent["instructions"],
        agent["lessons"],
        _feedback_lines(feedback, include_user=False),
    )
    database.update_user_agent(user["id"], lessons=result.lessons)
    database.mark_personal_consumed([f["id"] for f in feedback])
    logger.info("개인 에이전트 학습 완료 (%s): 피드백 %d건 반영", user["name"], len(feedback))
    return {
        "trained": True,
        "feedback_count": len(feedback),
        "lessons": result.lessons,
        "changelog": result.changelog,
    }

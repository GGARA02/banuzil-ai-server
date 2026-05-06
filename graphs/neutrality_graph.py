# ============================================================
# graphs/neutrality_graph.py
# 중립 검사 AI — LangGraph (2노드)
#
# 노드:
#   llm_judge  → DSPy NeutralityJudge로 채점
#   verdict    → pass/fail 판정 + 피드백 패키징 (LLM 없음)
#
# 위치: eft_graph.py 내 response_generator → self_refine 사이
# 입력: 이번 라운드 발화 + 응답 + couple_profile (히스토리 없음)
# 출력: neutrality_result dict → EFTState에 저장
# ============================================================

import asyncio
from typing import Any, Optional
from typing_extensions import TypedDict

from config.settings import (
    NEUTRALITY_PASS_SCORE, NEUTRALITY_WARN_SCORE,
)
from config.attachment_weight import CoupleProfile


# ── NeutralityState ───────────────────────────────────────
class NeutralityState(TypedDict):
    # 입력
    f_reply:        str
    m_reply:        str
    f_response:     str
    m_response:     str
    couple_profile: Any   # CoupleProfile
    eft_stage:      int

    # 출력
    neutrality_score:    float
    bias_direction:      str     # "toward_f" | "toward_m" | "none"
    violations:          list
    reasoning:           str
    feedback_for_regen:  str
    warning_hint:        str
    passed:              bool
    regen_triggered:     bool


# ── 노드 1: llm_judge (DSPy) ──────────────────────────────
async def node_llm_judge(state: NeutralityState) -> NeutralityState:
    """
    DSPy NeutralityJudge로 중립성 채점.
    동기 forward()를 run_in_executor로 래핑해 비동기 호환 유지.
    """
    from services.dspy_modules.neutrality_module import get_neutrality_judge
    judge = get_neutrality_judge()
    cp    = state["couple_profile"]
    loop  = asyncio.get_running_loop()

    result = await loop.run_in_executor(
        None,
        lambda: judge.forward(
            f_reply        = state["f_reply"],
            m_reply        = state["m_reply"],
            f_response     = state["f_response"],
            m_response     = state["m_response"],
            classification = cp.classification,
            eft_stage      = state["eft_stage"],
        )
    )

    return {
        **state,
        "neutrality_score":   result["neutrality_score"],
        "bias_direction":     result["bias_direction"],
        "violations":         result["violations"],
        "reasoning":          result["reasoning"],
        "feedback_for_regen": result["feedback_for_regen"],
    }


# ── 노드 2: verdict ────────────────────────────────────────
def node_verdict(state: NeutralityState) -> NeutralityState:
    """
    점수 기반 pass/fail 판정 + 피드백 패키징. LLM 없음.

    판정 흐름:
        score >= WARN(4) → clean pass
        PASS(3) <= score < WARN(4) → pass + warning_hint
        score < PASS(3) → fail → regen_triggered=True
    """
    score     = state["neutrality_score"]
    direction = state["bias_direction"]
    feedback  = state["feedback_for_regen"]
    violations = state["violations"]

    passed          = score >= NEUTRALITY_PASS_SCORE
    regen_triggered = not passed

    # warning_hint: 다음 라운드 프롬프트에 추가할 힌트
    warning_hint = ""
    if passed and score < NEUTRALITY_WARN_SCORE:
        dir_label = "여성" if direction == "toward_f" else "남성" if direction == "toward_m" else ""
        warning_hint = (
            f"[중립성 주의 — 직전 라운드 점수 {score:.0f}/5]\n"
            f"{'편향 방향: ' + dir_label + ' 쪽 / ' if dir_label else ''}"
            f"이번 라운드에서는 양측 발화를 더욱 균등하게 다루어 주십시오."
        )

    # regen 피드백: response_generator 재실행 시 프롬프트 끝에 추가
    regen_feedback = ""
    if regen_triggered and feedback:
        violations_text = "\n".join(f"  - {v}" for v in violations) if violations else "  (상세 내용 없음)"
        regen_feedback = (
            f"\n\n[⚠ 중립성 교정 지침 — 직전 응답 편향 감지]\n"
            f"편향 방향: {'여성 쪽' if direction == 'toward_f' else '남성 쪽' if direction == 'toward_m' else '불명확'}\n"
            f"감지된 문제:\n{violations_text}\n"
            f"교정 지침: {feedback}\n\n"
            f"재생성 시 반드시 준수:\n"
            f"  - 양측 발화를 동등한 비중으로 다룰 것\n"
            f"  - 갈등 원인을 어느 한쪽에 귀속시키지 말 것\n"
            f"  - 부정적 사이클 프레임을 명확히 사용할 것"
        )

    return {
        **state,
        "passed":           passed,
        "regen_triggered":  regen_triggered,
        "warning_hint":     warning_hint,
        "feedback_for_regen": regen_feedback,  # 재생성용으로 덮어쓰기
    }


# ── 독립 실행 함수 (eft_graph에서 직접 호출) ──────────────
async def run_neutrality_check(
    f_reply:        str,
    m_reply:        str,
    f_response:     str,
    m_response:     str,
    couple_profile: CoupleProfile,
    eft_stage:      int,
) -> dict:
    """
    eft_graph.py에서 직접 호출하는 함수.
    LangGraph 없이 노드 함수를 순서대로 실행.
    (LangGraph 서브그래프로 만들면 State 변환 복잡도가 높아짐 — 직접 호출이 더 명확)
    """
    state: NeutralityState = {
        "f_reply":           f_reply,
        "m_reply":           m_reply,
        "f_response":        f_response,
        "m_response":        m_response,
        "couple_profile":    couple_profile,
        "eft_stage":         eft_stage,
        "neutrality_score":  5.0,
        "bias_direction":    "none",
        "violations":        [],
        "reasoning":         "",
        "feedback_for_regen": "",
        "warning_hint":      "",
        "passed":            True,
        "regen_triggered":   False,
    }

    state = await node_llm_judge(state)
    state = node_verdict(state)

    return {
        "score":              state["neutrality_score"],
        "bias_direction":     state["bias_direction"],
        "violations":         state["violations"],
        "reasoning":          state["reasoning"],
        "passed":             state["passed"],
        "regen_triggered":    state["regen_triggered"],
        "warning_hint":       state["warning_hint"],
        "feedback_for_regen": state["feedback_for_regen"],
    }

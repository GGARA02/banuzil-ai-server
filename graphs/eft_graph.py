# ============================================================
# graphs/eft_graph.py
# 상담 AI — LangGraph 상태 그래프 (핵심)
#
# 노드 순서:
# risk_gate → bullet_detector → emotion_inject → eft_stage_router
# → response_generator → self_refine → stage_transition_check
#
# 특징:
# - f+m 동시 입력 → f+m 동시 출력 (asyncio.gather 병렬)
# - 단계 후퇴 없음 (1→2: 사이클 동의, 2→3: 신호 기반, 3 종료: 양측 동의)
# - risk_gate 하드코딩 키워드 기반 (감지해도 상담 계속, Spring이 처리)
# - 총알잡기: bullet_detector 노드가 감지 → response_generator에 컨텍스트 주입
# - KoELECTRA 결과: emotion_inject 노드에서 EmotionService 직접 임포트
# - bullet_detector/self_refine/neutrality_check: DSPy 모듈 사용
# ============================================================

import asyncio
import json
from typing import Any, Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from services.llm import call_llm, call_llm_with_history

from config.settings import (
    MODEL_NAME, EVAL_MODEL_NAME,
    MAX_OUTPUT_TOKENS, EVAL_MAX_TOKENS,
    MAX_REFINE,
    EVAL_WEIGHTS, EVAL_PASS_SCORE, SAFETY_GATE_SCORE,
    BULLET_DETECTION_ENABLED, BULLET_THRESHOLD,
    RISK_DETECTION_THRESHOLD, RISK_DETECTION_THRESHOLD_IPV,
    MIN_STAGE_ROUNDS,
    NEUTRALITY_PASS_SCORE,
)
from config.attachment_weight import CoupleProfile
from config.prompts.eft_base import (
    build_system_prompt, build_user_message,
    build_cycle_explore_prompt, build_cycle_definition_prompt,
)
from config.prompts.stage_prompts import (
    SignalState, build_stage_instruction, build_eval_prompt,
    STAGE1_SIGNALS, STAGE2_SIGNALS,
)

# 2단계가 이 라운드 수 이상 누적되고 양측 깊은 신호 합이 충분하면 3단계로 강제 진입
# (eval 모델이 신호를 보수적으로 잡아 2단계에 무한 정체하는 것을 방지)
STAGE2_FORCE_ROUNDS = 4

# 1단계가 이 라운드 수 이상 누적되면 신호가 미달이어도 사이클 절차로 진입
# (철회형 파트너가 신호를 잘 안 드러내 1단계에 질질 끌리는 것을 방지)
CYCLE_FORCE_ROUNDS = 3


# ── 위험 키워드 하드코딩 ──────────────────────────────────
# risk_gate는 LLM 없이 순수 키워드 매칭 (반드시 하드코딩)
_RISK_KEYWORDS_HARD = {
    "자해": ["자해", "손목", "긋"],
    "자살": ["자살", "죽고 싶", "죽고싶", "뛰어내리", "목매", "극단적 선택"],
    "폭행": ["때리", "죽이", "폭행", "칼로", "협박", "죽여"],
    "스토킹": ["스토킹", "신상", "감금", "따라다"],
    "데이트폭력": ["강제로", "강간", "성폭"],
}


# ── LangGraph State 정의 ──────────────────────────────────
class EFTState(TypedDict):
    # 세션 고정
    session_id:          str
    couple_profile:      Any          # CoupleProfile 객체

    # 설정 (모두 변수화)
    model_name:          str
    max_output_tokens:   int
    max_refine:          int
    bullet_enabled:      bool
    bullet_threshold:    float
    emotion_weight:      float

    # 현재 입력 (양측 동시)
    f_reply:             str
    m_reply:             str
    round_num:           int

    # 히스토리
    f_history:           list[dict]   # [{"role": "user"|"assistant", "content": str}]
    m_history:           list[dict]

    # EFT 상태
    eft_stage:           int           # 1 / 2 / 3
    eft_step:            int           # 1~9
    stage_rounds:        dict[str, int] # {"1": n, "2": n, "3": n}
    stage_progress:      int           # 0~100
    prev_eft_stage:      int
    signals:             Any           # SignalState
    cycle_definition:    str
    cycle_agreed:        dict[str, bool]  # {"f": bool, "m": bool}
    end_agreed:          dict[str, bool]  # {"f": bool, "m": bool}
    cycle_skip_until:    int            # 사이클 거부 시 재시도 라운드
    cycle_round:         int           # 사이클 탐색 라운드 (1 또는 2)
    needs_cycle_definition: bool
    is_cycle_round:      bool          # 사이클 진입 라운드 (질문 생략용)

    # KoELECTRA 감성 분석 결과
    f_emotion_data:      Optional[dict]
    m_emotion_data:      Optional[dict]

    # 총알잡기
    bullet_detected:     bool
    bullet_type:         str           # "Reactive" / "Mistrust" / "None"
    bullet_target:       str           # "f" / "m" / ""
    bullet_intervention: str

    # 생성 응답
    f_response:          str
    m_response:          str

    # Self-Refine
    refine_count:        int
    eval_scores:         dict
    refine_feedback:     str           # 미달 척도 + 개선 방향

    # 중립 검사 결과
    neutrality_result:   Optional[dict]  # run_neutrality_check 반환값
    neutrality_warning:  str             # 다음 라운드 warning_hint

    # 위험 신호
    risk_flag:           bool
    risk_category:       str
    risk_keywords_found: list[str]

    # 단계 전환 플래그
    stage_transition_flag: bool
    next_stage:          int

    # 사이클 정의 모드 플래그
    cycle_mode:          str           # "" / "explore_f" / "explore_m" / "define"

    # RAG — 과거 동일 커플 세션 참고 컨텍스트
    rag_context:         str           # 빈 문자열이면 미적용


_call_llm              = call_llm
_call_llm_with_history = call_llm_with_history


# ================================================================
# 노드 1: risk_gate — 하드코딩 위험 키워드 감지
# LLM 없이 순수 키워드 매칭. 감지해도 상담은 계속 진행, Spring이 별도 처리.
# ================================================================
def node_risk_gate(state: EFTState) -> EFTState:
    f_reply = state.get("f_reply", "")
    m_reply = state.get("m_reply", "")
    combined = f_reply + " " + m_reply

    ipv_risk = state["couple_profile"].ipv_risk_flag
    threshold = RISK_DETECTION_THRESHOLD_IPV if ipv_risk else RISK_DETECTION_THRESHOLD

    found_keywords = []
    risk_category  = ""

    for category, keywords in _RISK_KEYWORDS_HARD.items():
        for kw in keywords:
            if kw in combined:
                found_keywords.append(kw)
                risk_category = category

    # IPV 위험 결합에서 추가 데이트폭력 키워드 확장
    # 감정 표현 단어(무서워/두려워/겁나)는 EFT가 유도하는 정상적인
    # 취약성 표현과 충돌해 오탐이 많아 제외 — 물리적 단서만 유지
    if ipv_risk:
        extra_ipv = ["맞아", "맞았", "밀쳐"]
        for kw in extra_ipv:
            if kw in combined:
                found_keywords.append(kw)
                if not risk_category:
                    risk_category = "데이트폭력_의심"

    risk_flag = len(found_keywords) > 0

    return {
        **state,
        "risk_flag":          risk_flag,
        "risk_category":      risk_category,
        "risk_keywords_found": found_keywords,
    }


def edge_risk_gate(state: EFTState) -> str:
    # 위험 감지 여부와 무관하게 항상 상담 진행
    # risk_flag/risk_category/risk_keywords_found는 state에 기록되어
    # 최종 응답에 포함 → Spring이 별도 처리
    return "bullet_detector"


# ================================================================
# 노드 2: bullet_detector — 총알잡기 감지 (DSPy)
# BulletDetector.forward() 동기 호출을 run_in_executor로 병렬 실행.
# 설정으로 ON/OFF 가능.
# ================================================================
async def node_bullet_detector(state: EFTState) -> EFTState:
    if not state.get("bullet_enabled", BULLET_DETECTION_ENABLED):
        return {**state, "bullet_detected": False, "bullet_type": "None", "bullet_target": ""}

    from services.dspy_modules.bullet_module import get_bullet_detector
    detector  = get_bullet_detector()
    f_reply   = state.get("f_reply", "")
    m_reply   = state.get("m_reply", "")
    eft_stage = state["eft_stage"]
    threshold = state.get("bullet_threshold", BULLET_THRESHOLD)
    loop      = asyncio.get_running_loop()

    # DSPy Predict는 동기 → run_in_executor로 병렬 실행
    f_res, m_res = await asyncio.gather(
        loop.run_in_executor(None, lambda: detector.forward(f_reply, eft_stage, True)),
        loop.run_in_executor(None, lambda: detector.forward(m_reply, eft_stage, False)),
    )

    # 임계값 이상의 신뢰도만 총알로 판정
    f_bullet = f_res.get("bullet_detected", False) and f_res.get("confidence", 0) >= threshold
    m_bullet = m_res.get("bullet_detected", False) and m_res.get("confidence", 0) >= threshold

    bullet_detected     = f_bullet or m_bullet
    bullet_type         = (f_res if f_bullet else m_res).get("bullet_type", "None") if bullet_detected else "None"
    bullet_target       = ("f" if f_bullet else "m") if bullet_detected else ""
    bullet_intervention = (f_res if f_bullet else m_res).get("suggested_intervention", "") if bullet_detected else ""

    return {
        **state,
        "bullet_detected":    bullet_detected,
        "bullet_type":        bullet_type,
        "bullet_target":      bullet_target,
        "bullet_intervention": bullet_intervention,
    }


# ================================================================
# 노드 3: emotion_inject — KoELECTRA 감성 분석 결과 State 주입
# EmotionService 직접 임포트 (별도 HTTP 호출 없음)
# ================================================================
async def node_emotion_inject(state: EFTState) -> EFTState:
    try:
        from services.emotion_service import EmotionService
        service = EmotionService()
        cp = state["couple_profile"]
        loop = asyncio.get_running_loop()

        f_result, m_result = await asyncio.gather(
            loop.run_in_executor(
                None, lambda: service.analyze(
                    text=state["f_reply"],
                    gender="여성",
                    situation=cp.f_situation[:20] if cp.f_situation else "연애",
                )
            ),
            loop.run_in_executor(
                None, lambda: service.analyze(
                    text=state["m_reply"],
                    gender="남성",
                    situation=cp.m_situation[:20] if cp.m_situation else "연애",
                )
            ),
        )
        return {**state, "f_emotion_data": f_result, "m_emotion_data": m_result}
    except Exception as e:
        # KoELECTRA 서비스 실패 시 None으로 graceful fallback (상담 중단 없음)
        print(f"[emotion_inject] KoELECTRA 호출 실패: {e}")
        return {**state, "f_emotion_data": None, "m_emotion_data": None}


def _format_emotion_context(emotion_data: Optional[dict], label: str) -> str:
    """KoELECTRA 결과를 프롬프트 삽입용 텍스트로 변환"""
    if not emotion_data:
        return ""
    cats = emotion_data.get("category", [])
    dets = emotion_data.get("detail", [])
    cat_str = " / ".join(f"{c['label']}({c['score']:.2f})" for c in cats[:2]) if cats else "미확인"
    det_str = " / ".join(f"{d['label']}({d['score']:.2f})" for d in dets[:3]) if dets else "미확인"
    return f"{label}: 대분류 {cat_str} | 소분류 {det_str}"


# ================================================================
# 노드 4: eft_stage_router — EFT 단계/스텝 결정
# 현재 상태 기반으로 stage_instruction 조립
# ================================================================
def node_eft_stage_router(state: EFTState) -> EFTState:
    # stage_rounds 업데이트 (라운드 시작 시 현재 단계 카운트 증가)
    # DB JSONB는 항상 문자열 키 반환 → 문자열로 통일
    _raw = state.get("stage_rounds") or {}
    stage_rounds = {str(k): v for k, v in _raw.items()} if _raw else {"1": 0, "2": 0, "3": 0}
    stage        = state["eft_stage"]
    round_num    = state.get("round_num", 1)
    stage_key    = str(stage)
    stage_rounds[stage_key] = stage_rounds.get(stage_key, 0) + 1

    # 사이클 진입 조건 선행 체크 (응답 생성 전에 판단)
    is_cycle_round = False
    if stage == 1 and not state.get("cycle_definition"):
        cycle_skip_until = state.get("cycle_skip_until", 0)
        if round_num >= cycle_skip_until:
            signals = state.get("signals") or SignalState()
            keys1 = STAGE1_SIGNALS
            f_cnt = sum(1 for k in keys1 if signals.f.get(k))
            m_cnt = sum(1 for k in keys1 if signals.m.get(k))
            cur1  = stage_rounds.get("1", 0)
            # 정상 경로: 양측 모두 1단계 신호 2개 이상이면 사이클 진입
            if f_cnt >= 2 and m_cnt >= 2:
                is_cycle_round = True
            # 안전 게이트: 1단계가 너무 길어지면(CYCLE_FORCE_ROUNDS+) 신호 미달이어도 진입
            # (철회형 파트너가 신호를 안 드러내 1단계에 무한 정체하는 것 방지)
            elif cur1 >= CYCLE_FORCE_ROUNDS:
                is_cycle_round = True

    # 사이클 요청 라운드에 cycle_skip_until을 round+3으로 세팅.
    # 수락 시 → cycle_definition이 채워져 1→2 전환, skip값 무관.
    # 거절 시 → cycle_definition 비어있음 + round_num < skip → 2라운드 자동 스킵.
    new_cycle_skip = (round_num + 3) if is_cycle_round else state.get("cycle_skip_until", 0)

    return {
        **state,
        "stage_rounds":     stage_rounds,
        "is_cycle_round":   is_cycle_round,
        "cycle_skip_until": new_cycle_skip,
    }


# ================================================================
# 노드 5: response_generator — f+m 동시 생성 (핵심)
# asyncio.gather로 병렬 GPT 호출 → 편향 방지
# ================================================================
async def node_response_generator(state: EFTState) -> EFTState:
    cp         = state["couple_profile"]
    stage      = state["eft_stage"]
    signals    = state.get("signals") or SignalState()
    round_num  = state.get("round_num", 1)
    is_stage_first = stage != state.get("prev_eft_stage", stage)

    # 총알잡기 컨텍스트
    bullet_context = ""
    if state.get("bullet_detected"):
        bullet_context = (
            f"총알 유형: {state.get('bullet_type')} | 발화자: {state.get('bullet_target')} | "
            f"권장 개입: {state.get('bullet_intervention')}\n"
            "→ 총알잡기 5단계 프로토콜을 적용하라. "
            "공격적 발화를 즉각 타당화(Closed Validation)하고 대화를 부드럽게 재구성하라. "
            "단, 상대방을 비난하거나 정당화하지 마라."
        )

    # KoELECTRA 감성 컨텍스트
    emotion_context = ""
    f_em = _format_emotion_context(state.get("f_emotion_data"), "여성")
    m_em = _format_emotion_context(state.get("m_emotion_data"), "남성")
    if f_em or m_em:
        emotion_context = "\n".join(filter(None, [f_em, m_em]))

    # RAG 컨텍스트 (과거 동일 커플 세션, 사이클 정의 시점에 검색되어 주입됨)
    rag_context = state.get("rag_context", "")

    # 시스템 프롬프트 (내담자별)
    f_sys = build_system_prompt(
        is_female      = True,
        couple_profile = cp,
        eft_stage      = stage,
        stage_progress = state.get("stage_progress", 0),
        bullet_context = bullet_context,
        emotion_context= emotion_context,
        rag_context    = rag_context,
    )
    m_sys = build_system_prompt(
        is_female      = False,
        couple_profile = cp,
        eft_stage      = stage,
        stage_progress = state.get("stage_progress", 0),
        bullet_context = bullet_context,
        emotion_context= emotion_context,
        rag_context    = rag_context,
    )

    # 단계 지침 (3단계는 Step 8/9 분화를 위해 누적 라운드 전달)
    _sr = state.get("stage_rounds") or {}
    stage3_round = _sr.get("3", _sr.get(3, 0))
    f_stage_instruction = build_stage_instruction(True,  stage, signals, round_num=round_num, stage3_round=stage3_round)
    m_stage_instruction = build_stage_instruction(False, stage, signals, round_num=round_num, stage3_round=stage3_round)

    # 파트너 발화: 이번 라운드의 실제 발화를 사용 (상대방 말 전달용)
    f_hist = state.get("f_history", [])
    m_hist = state.get("m_history", [])
    # 이번 라운드 파트너 발화 (매 라운드 상대방의 실제 말을 전달)
    partner_reply_for_f = state.get("m_reply", "")  # 남성 발화 → 여성에게 전달
    partner_reply_for_m = state.get("f_reply", "")  # 여성 발화 → 남성에게 전달

    # refine 피드백 있으면 stage_instruction에 추가
    refine_fb = state.get("refine_feedback", "")
    neutrality_fb = ""
    # neutrality_result에서 재생성 피드백 + warning 반영
    nr = state.get("neutrality_result")
    if nr and not nr.get("passed"):
        neutrality_fb = nr.get("feedback_for_regen", "")
    warning_hint = state.get("neutrality_warning", "")

    if refine_fb or neutrality_fb or warning_hint:
        extra = "\n".join(filter(None, [refine_fb, neutrality_fb, warning_hint]))
        f_stage_instruction += f"\n\n{extra}"
        m_stage_instruction += f"\n\n{extra}"

    # 사이클 진입 라운드 여부 (eft_stage_router에서 선행 판단)
    is_cycle_round  = state.get("is_cycle_round", False)
    bullet_detected = state.get("bullet_detected", False)

    # 유저 메시지
    f_user_msg = build_user_message(
        is_female           = True,
        round_num           = round_num,
        is_stage_first      = is_stage_first,
        partner_last_reply  = partner_reply_for_f,
        self_reply          = state.get("f_reply", ""),
        stage_instruction   = f_stage_instruction,
        is_cycle_round      = is_cycle_round,
        bullet_detected     = bullet_detected,
    )
    m_user_msg = build_user_message(
        is_female           = False,
        round_num           = round_num,
        is_stage_first      = is_stage_first,
        partner_last_reply  = partner_reply_for_m,
        self_reply          = state.get("m_reply", ""),
        stage_instruction   = m_stage_instruction,
        is_cycle_round      = is_cycle_round,
        bullet_detected     = bullet_detected,
    )

    # 병렬 GPT 호출 (편향 방지 핵심)
    f_resp, m_resp = await asyncio.gather(
        _call_llm_with_history(f_sys, f_hist, f_user_msg,
                               model=state.get("model_name", MODEL_NAME),
                               max_tokens=state.get("max_output_tokens", MAX_OUTPUT_TOKENS)),
        _call_llm_with_history(m_sys, m_hist, m_user_msg,
                               model=state.get("model_name", MODEL_NAME),
                               max_tokens=state.get("max_output_tokens", MAX_OUTPUT_TOKENS)),
    )

    return {
        **state,
        "f_response":    f_resp,
        "m_response":    m_resp,
        "refine_feedback": "",   # 피드백 초기화
    }


# ================================================================
# 노드 5.5: neutrality_check — 중립성 검사
# response_generator 직후, self_refine 직전 실행
# ================================================================
async def node_neutrality_check(state: EFTState) -> EFTState:
    """
    중립 검사 AI 호출.
    fail 시 refine_feedback에 교정 지침 추가 → response_generator 재실행.
    pass + warning 시 warning_hint 저장 → 다음 라운드 프롬프트에 반영.
    """
    from graphs.neutrality_graph import run_neutrality_check

    try:
        result = await run_neutrality_check(
            f_reply        = state["f_reply"],
            m_reply        = state["m_reply"],
            f_response     = state["f_response"],
            m_response     = state["m_response"],
            couple_profile = state["couple_profile"],
            eft_stage      = state["eft_stage"],
        )
    except Exception as e:
        print(f"[neutrality_check] 오류 — 통과 처리: {e}")
        result = {
            "score": 5.0, "bias_direction": "none", "violations": [],
            "reasoning": "", "passed": True, "regen_triggered": False,
            "warning_hint": "", "feedback_for_regen": "",
        }

    refine_feedback = state.get("refine_feedback", "")
    if result["regen_triggered"]:
        refine_feedback = result["feedback_for_regen"]

    return {
        **state,
        "neutrality_result":  result,
        "neutrality_warning": result.get("warning_hint", ""),
        "refine_feedback":    refine_feedback,
    }


def edge_neutrality_check(state: EFTState) -> str:
    """중립 검사 실패 시 response_generator로 루프백. max_refine=0이면 self_refine 스킵."""
    nr = state.get("neutrality_result", {})
    refine_count = state.get("refine_count", 0)
    max_refine   = state.get("max_refine", MAX_REFINE)

    if max_refine == 0:
        return "stage_transition_check"
    if nr.get("regen_triggered") and refine_count < max_refine:
        return "response_generator"
    return "self_refine"


# ================================================================
# 노드 6: self_refine — 6개 척도 채점 + 재생성 루프 (DSPy)
# EFTEvaluator.forward() 동기 호출을 run_in_executor로 실행.
# ================================================================
async def node_self_refine(state: EFTState) -> EFTState:
    from services.dspy_modules.eval_module import get_eft_evaluator
    evaluator = get_eft_evaluator()
    cp        = state["couple_profile"]
    loop      = asyncio.get_running_loop()

    scores = await loop.run_in_executor(
        None,
        lambda: evaluator.forward(
            f_reply       = state.get("f_reply", ""),
            m_reply       = state.get("m_reply", ""),
            f_response    = state.get("f_response", ""),
            m_response    = state.get("m_response", ""),
            classification= cp.classification,
            f_attach_type = cp.f_profile.attach_type,
            m_attach_type = cp.m_profile.attach_type,
            eft_stage     = state["eft_stage"],
        )
    )

    # 가중 평균
    weighted_avg = sum(
        scores.get(k, 3.0) * w for k, w in EVAL_WEIGHTS.items()
    )
    scores["weighted_avg"] = round(weighted_avg, 3)

    refine_count = state.get("refine_count", 0)

    # 재생성 조건
    safety_fail  = scores.get("safety", 5.0) < SAFETY_GATE_SCORE
    quality_fail = weighted_avg < EVAL_PASS_SCORE and refine_count < state.get("max_refine", MAX_REFINE)

    if safety_fail or quality_fail:
        hints  = scores.get("improvement_hints", "")
        failed = [k for k, v in scores.items()
                  if k in EVAL_WEIGHTS and v < EVAL_PASS_SCORE]
        feedback = (
            f"[재생성 요청 — 미달 척도: {', '.join(failed)}]\n"
            f"개선 방향: {hints}"
        )
        return {
            **state,
            "eval_scores":     scores,
            "refine_count":    refine_count + 1,
            "refine_feedback": feedback,
        }

    # 통과
    return {
        **state,
        "eval_scores":     scores,
        "refine_feedback": "",
    }


def edge_self_refine(state: EFTState) -> str:
    """재생성 여부 결정"""
    feedback = state.get("refine_feedback", "")
    if feedback:
        return "response_generator"
    return "stage_transition_check"


# ================================================================
# 노드 7: stage_transition_check — 단계 전환 신호 감지
# EFT 진행도 평가 GPT 호출 → signals 누적 업데이트
# ================================================================
async def node_stage_transition_check(state: EFTState) -> EFTState:
    cp        = state["couple_profile"]
    signals   = state.get("signals") or SignalState()
    stage     = state["eft_stage"]
    round_num = state.get("round_num", 1)

    # 히스토리 요약 (최근 250자씩)
    f_hist = state.get("f_history", [])
    m_hist = state.get("m_history", [])
    f_summary = "\n".join(
        f"[여성-{'내담자' if h['role']=='user' else 'AI'}{i+1}] {h['content'][:250]}"
        for i, h in enumerate(f_hist)
    )
    m_summary = "\n".join(
        f"[남성-{'내담자' if h['role']=='user' else 'AI'}{i+1}] {h['content'][:250]}"
        for i, h in enumerate(m_hist)
    )

    eval_prompt = build_eval_prompt(
        eft_stage         = stage,
        stage_rounds      = state.get("stage_rounds", {}),
        signals           = signals,
        couple_combo      = cp.classification,
        f_type            = cp.f_profile.attach_type,
        f_anxiety         = cp.f_profile.anxiety_score,
        f_avoidance       = cp.f_profile.avoidance_score,
        m_type            = cp.m_profile.attach_type,
        m_anxiety         = cp.m_profile.anxiety_score,
        m_avoidance       = cp.m_profile.avoidance_score,
        round_num         = round_num,
        f_history_summary = f_summary,
        m_history_summary = m_summary,
    )

    raw = await _call_llm("", eval_prompt, model=EVAL_MODEL_NAME, max_tokens=EVAL_MAX_TOKENS)

    try:
        clean = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(clean)
    except Exception:
        result = {"stage": stage, "progress": 0, "f_signals": {}, "m_signals": {}}

    # 신호 누적 (한 번 True된 신호는 False로 돌아가지 않음)
    if result.get("f_signals"):
        signals.merge("f", result["f_signals"])
    if result.get("m_signals"):
        signals.merge("m", result["m_signals"])

    # 단계 후퇴 없음 보장
    new_stage = max(stage, min(3, result.get("stage", stage)))
    stage_rounds = state.get("stage_rounds", {})
    cur_rounds   = stage_rounds.get(str(stage), stage_rounds.get(stage, 0))

    # 1→2 전진: eval 모델이 아니라 코드가 직접 처리한다.
    # 사이클 정의가 존재하면(=사이클 탐색·정의 절차 완료) 1단계 최소 라운드 충족 시 2단계 진입.
    # (Spring/AI 어디에도 별도 '동의' 신호 통로가 없으므로 cycle_definition 존재를 진입 트리거로 사용)
    if stage == 1:
        if state.get("cycle_definition") and cur_rounds >= MIN_STAGE_ROUNDS:
            new_stage = 2
        else:
            new_stage = 1   # 그 외에는 1단계 유지 (eval 모델이 함부로 못 올림)
    elif stage == 2:
        # 양측 2단계 깊은 신호(취약성/공감/재관여/연화) 개수
        f_deep = sum(1 for k in STAGE2_SIGNALS if signals.f.get(k))
        m_deep = sum(1 for k in STAGE2_SIGNALS if signals.m.get(k))
        if new_stage >= 3 and cur_rounds >= MIN_STAGE_ROUNDS:
            new_stage = 3                       # eval이 신호 기반으로 3 제안
        elif cur_rounds >= STAGE2_FORCE_ROUNDS and (f_deep + m_deep) >= 2:
            new_stage = 3                       # 보조 게이트: 정체 방지 강제 진입
        else:
            new_stage = 2
    elif new_stage > stage and cur_rounds < MIN_STAGE_ROUNDS:
        new_stage = stage

    # 진행도: 3단계는 코드로 점증시키되, Step 8(합의)→Step 9(공고화) 시간을 확보한다.
    # s3 < 3 (Step 8: 일상 적용 합의) 동안은 75 이하로 묶어 조기 종료를 막고,
    # s3 >= 3 (Step 9: 변화 서사 공고화) 부터 90까지 올려, 공고화가 1~2라운드 진행된 뒤 종료되게 한다.
    if new_stage == 3:
        s3 = stage_rounds.get("3", 0)
        if s3 < 3:
            progress = min(75, 40 + s3 * 18)        # 40 → 58 → 75 (Step 8, 종료 방지)
        else:
            progress = min(100, 78 + (s3 - 3) * 12) # 78 → 90 → 100 (Step 9, 90 도달 종료)
    elif new_stage == 1:
        # 1단계 진행도: 1단계 신호(4종 × 양측 = 8칸) 누적 개수 기반 (eval 모델 의존 제거)
        on = (sum(1 for k in STAGE1_SIGNALS if signals.f.get(k))
              + sum(1 for k in STAGE1_SIGNALS if signals.m.get(k)))
        progress = min(90, round(on / 8 * 100))
    else:  # new_stage == 2
        # 2단계 진행도: 2단계 신호(4종 × 양측 = 8칸) 누적 개수 기반
        on = (sum(1 for k in STAGE2_SIGNALS if signals.f.get(k))
              + sum(1 for k in STAGE2_SIGNALS if signals.m.get(k)))
        progress = min(90, round(on / 8 * 100))

    # 1단계 사이클 동의 필요 여부 (eft_stage_router에서 선행 판단한 결과 활용)
    needs_cycle = state.get("is_cycle_round", False)

    stage_changed = new_stage != stage
    prev_stage    = stage

    return {
        **state,
        "eft_stage":              new_stage,
        "prev_eft_stage":         prev_stage,
        "stage_progress":         progress,
        "signals":                signals,
        "stage_transition_flag":  stage_changed,
        "next_stage":             new_stage if stage_changed else 0,
        "needs_cycle_definition": needs_cycle,
    }


# ================================================================
# 그래프 조립
# ================================================================
def build_eft_graph() -> StateGraph:
    graph = StateGraph(EFTState)

    # 노드 등록
    graph.add_node("risk_gate",              node_risk_gate)
    graph.add_node("bullet_detector",        node_bullet_detector)
    graph.add_node("emotion_inject",         node_emotion_inject)
    graph.add_node("eft_stage_router",       node_eft_stage_router)
    graph.add_node("response_generator",     node_response_generator)
    graph.add_node("neutrality_check",       node_neutrality_check)   # ← 신규
    graph.add_node("self_refine",            node_self_refine)
    graph.add_node("stage_transition_check", node_stage_transition_check)

    # 엣지 연결
    graph.set_entry_point("risk_gate")
    # risk_gate → 항상 bullet_detector (위험 감지해도 상담 계속, Spring이 risk_flag 처리)
    graph.add_edge("risk_gate", "bullet_detector")
    graph.add_edge("bullet_detector",    "emotion_inject")
    graph.add_edge("emotion_inject",     "eft_stage_router")
    graph.add_edge("eft_stage_router",   "response_generator")
    graph.add_edge("response_generator", "neutrality_check")           # ← 변경
    graph.add_conditional_edges("neutrality_check", edge_neutrality_check, {
        "response_generator":     "response_generator",                # ← 재생성 루프
        "self_refine":            "self_refine",
        "stage_transition_check": "stage_transition_check",            # ← max_refine=0 스킵
    })
    graph.add_conditional_edges("self_refine", edge_self_refine, {
        "response_generator":     "response_generator",
        "stage_transition_check": "stage_transition_check",
    })
    graph.add_edge("stage_transition_check", END)

    return graph.compile()


# 싱글턴 그래프 인스턴스
_eft_graph = None

def get_eft_graph():
    global _eft_graph
    if _eft_graph is None:
        _eft_graph = build_eft_graph()
    return _eft_graph


# ================================================================
# State 초기화 헬퍼
# ================================================================
def create_initial_state(
    session_id:     str,
    couple_profile: CoupleProfile,
    f_reply:        str,
    m_reply:        str,
    f_history:      list[dict] = None,
    m_history:      list[dict] = None,
    eft_stage:      int = 1,
    stage_rounds:   dict = None,
    stage_progress: int = 0,
    signals:        SignalState = None,
    cycle_definition: str = "",
    cycle_agreed:   dict = None,
    cycle_skip_until: int = 0,
    end_agreed:     dict = None,
    round_num:      int = 1,
    rag_context:    str = "",
    # 설정 오버라이드 (모두 변수화)
    model_name:     str = None,
    max_output_tokens: int = None,
    max_refine:     int = None,
    bullet_enabled: bool = None,
    bullet_threshold: float = None,
    emotion_weight: float = None,
) -> EFTState:
    from config.settings import EMOTION_WEIGHT
    return EFTState(
        session_id          = session_id,
        couple_profile      = couple_profile,
        model_name          = model_name or MODEL_NAME,
        max_output_tokens   = max_output_tokens or MAX_OUTPUT_TOKENS,
        max_refine          = max_refine if max_refine is not None else MAX_REFINE,
        bullet_enabled      = bullet_enabled if bullet_enabled is not None else BULLET_DETECTION_ENABLED,
        bullet_threshold    = bullet_threshold or BULLET_THRESHOLD,
        emotion_weight      = emotion_weight or EMOTION_WEIGHT,
        f_reply             = f_reply,
        m_reply             = m_reply,
        round_num           = round_num,
        f_history           = f_history or [],
        m_history           = m_history or [],
        eft_stage           = eft_stage,
        eft_step            = 1,
        stage_rounds        = stage_rounds or {"1": 0, "2": 0, "3": 0},
        stage_progress      = stage_progress,
        prev_eft_stage      = eft_stage,
        signals             = signals or SignalState(),
        cycle_definition    = cycle_definition,
        cycle_agreed        = cycle_agreed or {"f": False, "m": False},
        end_agreed          = end_agreed or {"f": False, "m": False},
        cycle_round         = 0,
        cycle_skip_until       = cycle_skip_until,
        needs_cycle_definition = False,
        is_cycle_round         = False,
        rag_context         = rag_context,
        f_emotion_data      = None,
        m_emotion_data      = None,
        bullet_detected     = False,
        bullet_type         = "None",
        bullet_target       = "",
        bullet_intervention = "",
        f_response          = "",
        m_response          = "",
        refine_count        = 0,
        eval_scores         = {},
        refine_feedback     = "",
        neutrality_result   = None,
        neutrality_warning  = "",
        risk_flag           = False,
        risk_category       = "",
        risk_keywords_found = [],
        stage_transition_flag = False,
        next_stage          = 0,
        cycle_mode          = "",
    )

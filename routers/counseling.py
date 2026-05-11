# ============================================================
# routers/counseling.py — 상담 AI 라우터 (Stateless 버전)
#
# Spring이 하는 것: 세션 생명주기, DB 영속화, 상태 추적
# Python이 하는 것: 요청마다 분석 → 결과 반환 (상태 미보유)
#
# 엔드포인트:
#   POST /ai/round-analyze — 라운드 분석 (핵심)
#   POST /ai/cycle         — 사이클 탐색/정의
#   POST /ai/report        — 최종 보고서
# ============================================================

import asyncio
from fastapi import APIRouter, HTTPException

from schemas.request import RoundAnalyzeRequest, CycleRequest, ReportRequest
from schemas.response import (
    RoundAnalyzeResponse,
    CycleExploreResponse, CycleDefinitionResponse,
    ReportResponse,
)
from services.attachment_service import build_couple_profile
from services.report_service import generate_report
from graphs.eft_graph import get_eft_graph, create_initial_state
from services.llm import call_llm
from config.prompts.stage_prompts import SignalState
from config.prompts.eft_base import (
    build_cycle_explore_prompt, build_cycle_definition_prompt,
)
from config.settings import EVAL_MODEL_NAME

router = APIRouter(prefix="/ai", tags=["ai"])


def _signals_to_dict(signals: SignalState) -> dict:
    return {"f": dict(signals.f), "m": dict(signals.m)}


def _signals_from_dict(d: dict | None) -> SignalState:
    s = SignalState()
    if not d:
        return s
    if "f" in d:
        s.f.update(d["f"])
    if "m" in d:
        s.m.update(d["m"])
    return s


# ================================================================
# POST /ai/round-analyze — 라운드 분석
# ================================================================
@router.post("/round-analyze", response_model=RoundAnalyzeResponse)
async def round_analyze(req: RoundAnalyzeRequest):
    """
    EFT 상담 1라운드 분석.
    Spring이 모든 컨텍스트를 보내면, AI가 분석 후 결과 + 업데이트된 상태를 반환.
    Spring은 반환된 updated_* 필드를 DB에 저장하고 다음 라운드에 재전달.
    """
    couple_profile = build_couple_profile(
        session_id  = req.session_id,
        f_anxiety   = req.f_anxiety,
        f_avoidance = req.f_avoidance,
        f_mbti      = req.f_mbti or "",
        f_situation = "",
        m_anxiety   = req.m_anxiety,
        m_avoidance = req.m_avoidance,
        m_mbti      = req.m_mbti or "",
        m_situation = "",
    )

    signals = _signals_from_dict(req.signals)

    state = create_initial_state(
        session_id       = req.session_id,
        couple_profile   = couple_profile,
        f_reply          = req.f_reply,
        m_reply          = req.m_reply,
        f_history        = req.f_history,
        m_history        = req.m_history,
        eft_stage        = req.eft_stage,
        stage_rounds     = req.stage_rounds,
        stage_progress   = req.stage_progress,
        signals          = signals,
        cycle_definition = req.cycle_definition,
        round_num        = req.round_num,
        model_name       = req.model_name,
        bullet_enabled   = req.bullet_enabled,
        emotion_weight   = req.emotion_weight,
    )

    try:
        graph  = get_eft_graph()
        result = await graph.ainvoke(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"상담 AI 오류: {str(e)}")

    f_resp = result.get("f_response", "")
    m_resp = result.get("m_response", "")

    # 히스토리에 현재 라운드 추가
    updated_f_history = req.f_history + [
        {"role": "user",      "content": req.f_reply},
        {"role": "assistant", "content": f_resp},
    ]
    updated_m_history = req.m_history + [
        {"role": "user",      "content": req.m_reply},
        {"role": "assistant", "content": m_resp},
    ]

    # 위험 감지 시
    if result.get("risk_flag"):
        return RoundAnalyzeResponse(
            session_id             = req.session_id,
            f_message              = "지금 당신의 안전이 가장 중요합니다. 상담을 잠시 중단합니다.",
            m_message              = "지금 당신의 안전이 가장 중요합니다. 상담을 잠시 중단합니다.",
            updated_eft_stage      = req.eft_stage,
            updated_stage_rounds   = req.stage_rounds,
            updated_stage_progress = req.stage_progress,
            updated_signals        = _signals_to_dict(signals),
            updated_f_history      = updated_f_history,
            updated_m_history      = updated_m_history,
            risk_flag              = True,
            risk_category          = result.get("risk_category", ""),
            risk_keywords          = result.get("risk_keywords_found", []),
        )

    updated_signals = result.get("signals", signals)
    eval_scores     = result.get("eval_scores") or {}
    nr              = result.get("neutrality_result") or {}

    neutrality_for_spring = {
        "score":           nr.get("score"),
        "bias_direction":  nr.get("bias_direction", "none"),
        "passed":          nr.get("passed", True),
        "violations":      nr.get("violations", []),
        "regen_triggered": nr.get("regen_triggered", False),
    } if nr else None

    return RoundAnalyzeResponse(
        session_id               = req.session_id,
        f_message                = f_resp,
        m_message                = m_resp,
        updated_eft_stage        = result.get("eft_stage", req.eft_stage),
        updated_stage_rounds     = result.get("stage_rounds", req.stage_rounds),
        updated_stage_progress   = result.get("stage_progress", req.stage_progress),
        updated_signals          = _signals_to_dict(updated_signals),
        updated_f_history        = updated_f_history,
        updated_m_history        = updated_m_history,
        needs_cycle_definition   = result.get("needs_cycle_definition", False),
        bullet_detected          = result.get("bullet_detected", False),
        bullet_type              = result.get("bullet_type", "None"),
        eval_score               = eval_scores.get("weighted_avg"),
        eval_scores              = eval_scores or None,
        neutrality_result        = neutrality_for_spring,
        f_emotion                = result.get("f_emotion_data"),
        m_emotion                = result.get("m_emotion_data"),
        risk_keywords            = result.get("risk_keywords_found", []),
    )


# ================================================================
# POST /ai/cycle — 사이클 탐색/정의
# ================================================================
@router.post("/cycle")
async def cycle_analyze(req: CycleRequest):
    """
    사이클 탐색 또는 정의.
    cycle_definition이 비어있으면 → 탐색 질문 생성
    cycle_definition이 있으면   → 사이클 정의 생성
    동의 처리는 Spring이 DB에서 직접 관리.
    """
    f_hist_text = "\n".join(
        f"{'여성' if h['role']=='user' else 'AI'}: {h['content'][:200]}"
        for h in req.f_history
    )
    m_hist_text = "\n".join(
        f"{'남성' if h['role']=='user' else 'AI'}: {h['content'][:200]}"
        for h in req.m_history
    )

    if not req.cycle_definition:
        f_q, m_q = await asyncio.gather(
            call_llm("", build_cycle_explore_prompt(True,  f_hist_text),
                      model=EVAL_MODEL_NAME, max_tokens=400),
            call_llm("", build_cycle_explore_prompt(False, m_hist_text),
                      model=EVAL_MODEL_NAME, max_tokens=400),
        )
        return CycleExploreResponse(
            session_id  = req.session_id,
            f_question  = f_q.strip(),
            m_question  = m_q.strip(),
            cycle_round = 1,
        )
    else:
        definition = await call_llm(
            "", build_cycle_definition_prompt(f_hist_text, m_hist_text),
            model=EVAL_MODEL_NAME, max_tokens=600,
        )
        return CycleDefinitionResponse(
            session_id       = req.session_id,
            cycle_definition = definition.strip(),
        )


# ================================================================
# POST /ai/report — 최종 보고서
# ================================================================
@router.post("/report", response_model=ReportResponse)
async def generate_final_report(req: ReportRequest):
    """
    세션 히스토리 전체를 바탕으로 최종 보고서 생성.
    Spring이 DB에서 꺼낸 히스토리 + 프로필을 전달.
    """
    couple_profile = build_couple_profile(
        session_id  = req.session_id,
        f_anxiety   = req.f_anxiety,
        f_avoidance = req.f_avoidance,
        f_mbti      = req.f_mbti or "",
        f_situation = "",
        m_anxiety   = req.m_anxiety,
        m_avoidance = req.m_avoidance,
        m_mbti      = req.m_mbti or "",
        m_situation = "",
    )

    try:
        f_report, m_report = await generate_report(
            couple_profile   = couple_profile,
            f_history        = req.f_history,
            m_history        = req.m_history,
            cycle_definition = req.cycle_definition,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"보고서 생성 오류: {str(e)}")

    return ReportResponse(
        session_id = req.session_id,
        f_report   = f_report,
        m_report   = m_report,
    )

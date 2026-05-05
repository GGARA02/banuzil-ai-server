# ============================================================
# routers/counseling.py — 상담 AI 라우터 (Stateful 버전)
#
# Spring이 하는 것: session_id 전달, 텍스트 수신
# Python이 하는 것: 세션 상태 전체 관리, 히스토리 누적
#
# 엔드포인트:
#   POST /counseling/session  — 세션 생성 (최초 1회)
#   POST /counseling/round    — 라운드 실행 (매번)
#   POST /counseling/cycle    — 사이클 탐색/정의/동의
#   POST /counseling/report   — 최종 보고서
#   POST /counseling/end      — 3단계 종료 동의
#   DELETE /counseling/session/{id} — 세션 삭제
# ============================================================

import asyncio
from fastapi import APIRouter, HTTPException

from schemas.request import (
    SessionCreateRequest, CounselingRoundRequest,
    CycleConsentRequest, ReportRequest, EndConsentRequest,
)
from schemas.response import (
    SessionCreateResponse, CounselingRoundResponse,
    CycleExploreResponse, CycleDefinitionResponse,
    CycleAgreedResponse, CounselingReportResponse,
)
from services.attachment_service import build_couple_profile
from services.session_store import session_get, session_set, session_delete, session_exists
from services.report_service import generate_report
from graphs.eft_graph import get_eft_graph, create_initial_state
from services.llm import call_llm
from config.prompts.stage_prompts import SignalState
from config.prompts.eft_base import (
    build_cycle_explore_prompt, build_cycle_definition_prompt,
)
from config.settings import EVAL_MODEL_NAME

router = APIRouter(prefix="/counseling", tags=["counseling"])


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
# POST /counseling/session — 세션 생성 (최초 1회)
# ================================================================
@router.post("/session", response_model=SessionCreateResponse)
async def create_session(req: SessionCreateRequest):
    """
    상담 세션 생성.
    ECR-R + MBTI 기반으로 커플 프로파일 조립 후 세션에 저장.
    situation 없음 — 첫 발화부터 자연스럽게 시작.
    """
    if session_exists(req.session_id):
        raise HTTPException(status_code=409, detail="이미 존재하는 세션입니다.")

    couple_profile = build_couple_profile(
        session_id  = req.session_id,
        f_anxiety   = req.f_anxiety,
        f_avoidance = req.f_avoidance,
        f_mbti      = req.f_mbti or "",
        f_situation = "",   # situation 없음
        m_anxiety   = req.m_anxiety,
        m_avoidance = req.m_avoidance,
        m_mbti      = req.m_mbti or "",
        m_situation = "",
    )

    # 세션 초기 상태 저장
    session_set(req.session_id, {
        "couple_profile":   couple_profile,
        "f_history":        [],   # 누적 대화 히스토리
        "m_history":        [],
        "eft_stage":        1,
        "eft_step":         1,
        "stage_rounds":     {1: 0, 2: 0, 3: 0},
        "stage_progress":   0,
        "signals":          SignalState(),
        "cycle_definition": "",
        "cycle_agreed":     {"f": False, "m": False},
        "end_agreed":       {"f": False, "m": False},
        "round_num":        0,
        "model_name":       req.model_name,
        "bullet_enabled":   req.bullet_enabled,
        "emotion_weight":   req.emotion_weight,
    })

    return SessionCreateResponse(
        session_id     = req.session_id,
        classification = couple_profile.classification,
        ipv_risk_flag  = couple_profile.ipv_risk_flag,
    )


# ================================================================
# POST /counseling/round — 라운드 실행
# ================================================================
@router.post("/round", response_model=CounselingRoundResponse)
async def counseling_round(req: CounselingRoundRequest):
    """
    EFT 상담 1라운드 실행.
    세션에서 상태를 꺼내고, 실행 후 다시 저장.
    Spring은 f_reply + m_reply만 보내면 됨.
    """
    sess = session_get(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="세션이 없거나 만료되었습니다. /counseling/session으로 먼저 생성하세요.")

    # 세션에서 상태 복원
    couple_profile = sess["couple_profile"]
    round_num      = sess["round_num"] + 1
    signals        = sess["signals"]

    # LangGraph State 조립
    state = create_initial_state(
        session_id       = req.session_id,
        couple_profile   = couple_profile,
        f_reply          = req.f_reply,
        m_reply          = req.m_reply,
        f_history        = sess["f_history"],
        m_history        = sess["m_history"],
        eft_stage        = sess["eft_stage"],
        stage_rounds     = sess["stage_rounds"],
        stage_progress   = sess["stage_progress"],
        signals          = signals,
        cycle_definition = sess["cycle_definition"],
        cycle_agreed     = sess["cycle_agreed"],
        end_agreed       = sess["end_agreed"],
        round_num        = round_num,
        model_name       = sess.get("model_name"),
        bullet_enabled   = sess.get("bullet_enabled"),
        emotion_weight   = sess.get("emotion_weight"),
    )

    # LangGraph 실행
    try:
        graph  = get_eft_graph()
        result = await graph.ainvoke(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"상담 AI 오류: {str(e)}")

    f_resp = result.get("f_response", "")
    m_resp = result.get("m_response", "")

    # ── 히스토리 누적 ──────────────────────────────────────
    # f발화1 → AI답변1 → f발화2 → AI답변2 ... 순서로 쌓임
    updated_f_history = sess["f_history"] + [
        {"role": "user",      "content": req.f_reply},
        {"role": "assistant", "content": f_resp},
    ]
    updated_m_history = sess["m_history"] + [
        {"role": "user",      "content": req.m_reply},
        {"role": "assistant", "content": m_resp},
    ]

    # ── 세션 업데이트 ──────────────────────────────────────
    sess.update({
        "f_history":        updated_f_history,
        "m_history":        updated_m_history,
        "eft_stage":        result.get("eft_stage", sess["eft_stage"]),
        "stage_rounds":     result.get("stage_rounds", sess["stage_rounds"]),
        "stage_progress":   result.get("stage_progress", sess["stage_progress"]),
        "signals":          result.get("signals", signals),
        "cycle_definition": result.get("cycle_definition", sess["cycle_definition"]),
        "round_num":        round_num,
        "needs_cycle_definition": result.get("needs_cycle_definition", False),
    })
    session_set(req.session_id, sess)

    # 위험 감지 시
    if result.get("risk_flag"):
        return CounselingRoundResponse(
            session_id    = req.session_id,
            f_message     = "지금 당신의 안전이 가장 중요합니다. 상담을 잠시 중단합니다.",
            m_message     = "지금 당신의 안전이 가장 중요합니다. 상담을 잠시 중단합니다.",
            eft_stage     = sess["eft_stage"],
            risk_flag     = True,
            risk_category = result.get("risk_category", ""),
        )

    eval_scores  = result.get("eval_scores") or {}
    weighted_avg = eval_scores.get("weighted_avg")

    # neutrality_result에서 Spring 전달용 필드만 추출
    nr = result.get("neutrality_result") or {}
    neutrality_for_spring = {
        "score":           nr.get("score"),
        "bias_direction":  nr.get("bias_direction", "none"),
        "passed":          nr.get("passed", True),
        "violations":      nr.get("violations", []),
        "regen_triggered": nr.get("regen_triggered", False),
    } if nr else None

    return CounselingRoundResponse(
        session_id              = req.session_id,
        f_message               = f_resp,
        m_message               = m_resp,
        eft_stage               = result.get("eft_stage", sess["eft_stage"]),
        needs_cycle_definition  = result.get("needs_cycle_definition", False),
        stage_progress          = result.get("stage_progress", 0),
        bullet_detected         = result.get("bullet_detected", False),
        eval_score              = weighted_avg,
        neutrality_result       = neutrality_for_spring,
    )


# ================================================================
# POST /counseling/cycle — 사이클 탐색/정의/동의
# ================================================================
@router.post("/cycle")
async def counseling_cycle(req: CycleConsentRequest):
    sess = session_get(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    # 양측 동의 완료 → 2단계 진입
    if req.f_agreed and req.m_agreed:
        sess["eft_stage"]    = 2
        sess["cycle_agreed"] = {"f": True, "m": True}
        session_set(req.session_id, sess)
        return CycleAgreedResponse(session_id=req.session_id)

    # 히스토리 텍스트 변환
    f_hist_text = "\n".join(
        f"{'여성' if h['role']=='user' else 'AI'}: {h['content'][:200]}"
        for h in sess["f_history"]
    )
    m_hist_text = "\n".join(
        f"{'남성' if h['role']=='user' else 'AI'}: {h['content'][:200]}"
        for h in sess["m_history"]
    )

    cycle_def = sess.get("cycle_definition", "")

    if not cycle_def:
        # 탐색 질문 생성
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
        # 사이클 정의 생성
        definition = await call_llm(
            "", build_cycle_definition_prompt(f_hist_text, m_hist_text),
            model=EVAL_MODEL_NAME, max_tokens=600,
        )
        definition = definition.strip()
        sess["cycle_definition"] = definition
        session_set(req.session_id, sess)
        return CycleDefinitionResponse(
            session_id       = req.session_id,
            cycle_definition = definition,
        )


# ================================================================
# POST /counseling/end — 3단계 종료 동의
# ================================================================
@router.post("/end")
async def counseling_end(req: EndConsentRequest):
    sess = session_get(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    sess["end_agreed"] = {"f": req.f_agreed, "m": req.m_agreed}
    session_set(req.session_id, sess)

    both_agreed = req.f_agreed and req.m_agreed
    return {
        "session_id":  req.session_id,
        "both_agreed": both_agreed,
        "message":     "양측 동의 완료. /counseling/report를 호출하세요." if both_agreed else "한쪽이 아직 동의하지 않았습니다.",
    }


# ================================================================
# POST /counseling/report — 최종 보고서
# ================================================================
@router.post("/report", response_model=CounselingReportResponse)
async def counseling_report(req: ReportRequest):
    """
    세션 히스토리 전체를 바탕으로 최종 보고서 생성.
    Spring은 session_id만 보내면 됨.
    """
    sess = session_get(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    try:
        f_report, m_report = await generate_report(
            couple_profile   = sess["couple_profile"],
            f_history        = sess["f_history"],
            m_history        = sess["m_history"],
            cycle_definition = sess.get("cycle_definition", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"보고서 생성 오류: {str(e)}")

    return CounselingReportResponse(
        session_id = req.session_id,
        f_report   = f_report,
        m_report   = m_report,
    )


# ================================================================
# DELETE /counseling/session/{session_id} — 세션 삭제
# ================================================================
@router.delete("/session/{session_id}")
async def delete_session(session_id: str):
    if not session_exists(session_id):
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    session_delete(session_id)
    return {"session_id": session_id, "status": "deleted"}

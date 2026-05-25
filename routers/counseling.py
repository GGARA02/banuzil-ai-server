# ============================================================
# routers/counseling.py — 상담 AI 라우터 (DB-centric 버전)
#
# AI 서버가 Supabase에서 직접 세션 상태를 조회하고 저장한다.
# Spring은 session_id + 발화만 전달.
#
# 엔드포인트:
#   POST /ai/round-analyze — 라운드 분석 (핵심)
#   POST /ai/cycle         — 사이클 탐색/정의
#   POST /ai/report        — 최종 보고서
# ============================================================

import asyncio
import logging
from fastapi import APIRouter, HTTPException

from schemas.request import RoundAnalyzeRequest, CycleRequest, ReportRequest
from schemas.response import (
    RoundAnalyzeResponse,
    CycleExploreResponse, CycleDefinitionResponse,
    ReportResponse, ReportSections,
)
from services.session_service import (
    fetch_session_context, save_round_result,
    save_cycle_definition, save_report,
)
from services.report_service import generate_report
from graphs.eft_graph import get_eft_graph, create_initial_state
from services.llm import call_llm, call_llm_with_history
from config.prompts.eft_base import (
    build_cycle_explore_prompt, build_cycle_definition_prompt,
    build_system_prompt, build_cycle_bridge_message_prompt,
)
from config.settings import EVAL_MODEL_NAME, MODEL_NAME, RAG_ENABLED

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"])


# ================================================================
# POST /ai/round-analyze — 라운드 분석
# ================================================================
@router.post("/round-analyze", response_model=RoundAnalyzeResponse)
async def round_analyze(req: RoundAnalyzeRequest):
    """
    EFT 상담 1라운드 분석.
    1. DB에서 세션 컨텍스트 조회
    2. LangGraph 실행
    3. 결과 DB 저장
    4. Spring에 메시지 + 플래그만 반환
    """
    # 1. DB 조회
    try:
        ctx = await fetch_session_context(req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    # 2. 그래프 실행
    state = create_initial_state(
        session_id       = str(req.session_id),
        couple_profile   = ctx["couple_profile"],
        f_reply          = req.f_reply,
        m_reply          = req.m_reply,
        f_history        = ctx["f_history"],
        m_history        = ctx["m_history"],
        eft_stage        = ctx["eft_stage"],
        stage_rounds     = ctx["stage_rounds"],
        stage_progress   = ctx["stage_progress"],
        signals          = ctx["signals"],
        cycle_definition = ctx["cycle_definition"],
        cycle_skip_until = ctx["cycle_skip_until"],
        round_num        = ctx["round_num"],
        rag_context      = ctx["rag_context"],
        bullet_enabled   = req.bullet_enabled,
        max_refine       = req.max_refine,
    )

    try:
        graph  = get_eft_graph()
        result = await graph.ainvoke(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"상담 AI 오류: {str(e)}")

    # 3. DB 저장
    try:
        await save_round_result(req.session_id, ctx, result)
    except Exception as e:
        logger.error(f"[{req.session_id}] DB 저장 실패: {e}")

    # 4. 응답
    return RoundAnalyzeResponse(
        session_id             = req.session_id,
        f_message              = result.get("f_response", ""),
        m_message              = result.get("m_response", ""),
        needs_cycle_definition = result.get("needs_cycle_definition", False),
        risk_flag              = result.get("risk_flag", False),
        eft_stage              = result.get("eft_stage", ctx["eft_stage"]),
        stage_progress         = result.get("stage_progress", ctx["stage_progress"]),
    )


# ================================================================
# POST /ai/cycle — 사이클 탐색/정의
# ================================================================
@router.post("/cycle")
async def cycle_analyze(req: CycleRequest):
    """
    사이클 탐색 또는 정의.
    답변 없이 호출 → 탐색 질문 생성
    답변 포함 호출 → 사이클 정의 생성
    """
    # DB에서 히스토리 조회
    try:
        ctx = await fetch_session_context(req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    f_hist_text = "\n".join(
        f"{'여성' if h['role']=='user' else 'AI'}: {h['content'][:200]}"
        for h in ctx["f_history"]
    )
    m_hist_text = "\n".join(
        f"{'남성' if h['role']=='user' else 'AI'}: {h['content'][:200]}"
        for h in ctx["m_history"]
    )

    if not req.f_explore_answer and not req.m_explore_answer:
        f_q, m_q = await asyncio.gather(
            call_llm("", build_cycle_explore_prompt(True,  f_hist_text),
                      model=EVAL_MODEL_NAME, max_tokens=400),
            call_llm("", build_cycle_explore_prompt(False, m_hist_text),
                      model=EVAL_MODEL_NAME, max_tokens=400),
        )
        return CycleExploreResponse(
            session_id = req.session_id,
            f_question = f_q.strip(),
            m_question = m_q.strip(),
        )
    else:
        definition = await call_llm(
            "", build_cycle_definition_prompt(
                f_hist_text, m_hist_text,
                f_explore_answer=req.f_explore_answer,
                m_explore_answer=req.m_explore_answer,
            ),
            model=EVAL_MODEL_NAME, max_tokens=600,
        )
        definition = definition.strip()

        await save_cycle_definition(req.session_id, definition)

        # RAG: 사이클 정의 생성 직후 동일 커플 과거 세션 검색.
        # 히트 시 보고서 전문을 mediation_sessions.rag_context에 저장 → 2단계부터 프롬프트 주입.
        if RAG_ENABLED:
            try:
                from services.rag.retrieval_service import find_best_past_session
                from services.rag.rag_prompt_builder import build_rag_context
                from services.supabase_client import supa

                best = await find_best_past_session(
                    f_user_id          = ctx["f_user_id"],
                    m_user_id          = ctx["m_user_id"],
                    current_session_id = req.session_id,
                    cycle_definition   = definition,
                )
                rag_context = build_rag_context(best)
                if rag_context:
                    await supa.update(
                        "mediation_sessions",
                        {"session_id": f"eq.{req.session_id}"},
                        {"rag_context": rag_context},
                    )
            except Exception as e:
                logger.warning(f"[{req.session_id}] RAG 검색 실패 (무시): {e}")

        # 사이클 정의 직후 상담사 브릿지 메시지 생성 (2단계로 연결).
        # 실제 상담 발화이므로 MODEL_NAME 사용. 생성 실패해도 정의 응답 자체는 보장.
        f_message, m_message = "", ""
        try:
            cp    = ctx["couple_profile"]
            f_sys = build_system_prompt(
                is_female=True, couple_profile=cp,
                eft_stage=ctx["eft_stage"], stage_progress=ctx["stage_progress"],
            )
            m_sys = build_system_prompt(
                is_female=False, couple_profile=cp,
                eft_stage=ctx["eft_stage"], stage_progress=ctx["stage_progress"],
            )
            f_message, m_message = await asyncio.gather(
                call_llm_with_history(f_sys, ctx["f_history"],
                    build_cycle_bridge_message_prompt(True, definition), model=MODEL_NAME),
                call_llm_with_history(m_sys, ctx["m_history"],
                    build_cycle_bridge_message_prompt(False, definition), model=MODEL_NAME),
            )
            f_message = (f_message or "").strip()
            m_message = (m_message or "").strip()
        except Exception as e:
            logger.warning(f"[{req.session_id}] 브릿지 메시지 생성 실패 (무시): {e}")

        return CycleDefinitionResponse(
            session_id       = req.session_id,
            cycle_definition = definition,
            f_message        = f_message,
            m_message        = m_message,
        )


# ================================================================
# POST /ai/report — 최종 보고서
# ================================================================
@router.post("/report", response_model=ReportResponse)
async def generate_final_report(req: ReportRequest):
    """
    세션 히스토리 전체를 바탕으로 최종 보고서 생성.
    DB에서 모든 데이터를 직접 조회.
    """
    try:
        ctx = await fetch_session_context(req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        f_sections, m_sections = await generate_report(
            couple_profile   = ctx["couple_profile"],
            f_history        = ctx["f_history"],
            m_history        = ctx["m_history"],
            cycle_definition = ctx["cycle_definition"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"보고서 생성 오류: {str(e)}")

    # DB 저장
    try:
        await save_report(
            req.session_id,
            ctx["f_user_id"], ctx["m_user_id"],
            f_sections, m_sections,
        )
    except Exception as e:
        logger.error(f"[{req.session_id}] 보고서 DB 저장 실패: {e}")

    # RAG: 세션 사이클 정의를 벡터화하여 저장 (미래 세션 검색용).
    # 사이클 정의가 없으면 자동 스킵. 실패해도 보고서 응답엔 영향 없음.
    if RAG_ENABLED:
        try:
            from services.rag.embedding_service import save_session_embedding
            await save_session_embedding(
                session_id       = req.session_id,
                f_user_id        = ctx["f_user_id"],
                m_user_id        = ctx["m_user_id"],
                cycle_definition = ctx["cycle_definition"],
                f_report         = f_sections,
                m_report         = m_sections,
                eft_stage        = ctx["eft_stage"],
            )
        except Exception as e:
            logger.warning(f"[{req.session_id}] 임베딩 저장 실패 (무시): {e}")

    return ReportResponse(
        session_id = req.session_id,
        f_report   = ReportSections(**f_sections),
        m_report   = ReportSections(**m_sections),
    )

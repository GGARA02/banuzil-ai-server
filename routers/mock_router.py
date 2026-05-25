# ============================================================
# routers/mock_router.py — 테스트 전용 Mock DB 엔드포인트
#
# test_client.html에서 호출하여 인메모리 Mock DB를 세팅한다.
# 실제 Supabase 없이 AI 응답을 테스트할 수 있다.
# ============================================================

import logging
from fastapi import APIRouter
from pydantic import BaseModel, Field

import services.supabase_client as supa_module
from services.mock_db import mock_supa, seed_mock_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai/mock", tags=["mock"])


class MockInitRequest(BaseModel):
    session_id: int = Field(..., description="테스트 세션 ID")
    f_anxiety: float = Field(4.5, ge=1, le=7)
    f_avoidance: float = Field(2.1, ge=1, le=7)
    f_mbti: str = Field("ENFP", max_length=4)
    m_anxiety: float = Field(1.8, ge=1, le=7)
    m_avoidance: float = Field(4.2, ge=1, le=7)
    m_mbti: str = Field("ISTJ", max_length=4)


@router.post("/init-session")
async def mock_init_session(req: MockInitRequest):
    """
    Mock DB에 테스트 세션을 시드하고,
    supabase_client.supa를 mock으로 교체한다.
    이후 /ai/round-analyze 등은 mock DB를 사용한다.
    """
    seed_mock_session(
        session_id  = req.session_id,
        f_anxiety   = req.f_anxiety,
        f_avoidance = req.f_avoidance,
        f_mbti      = req.f_mbti,
        m_anxiety   = req.m_anxiety,
        m_avoidance = req.m_avoidance,
        m_mbti      = req.m_mbti,
    )

    # 활성 클라이언트를 mock으로 전환 (프록시가 위임)
    supa_module.set_mock_client(mock_supa)

    logger.info(f"[Mock] supa -> MockDB로 교체, 세션 #{req.session_id}")

    return {
        "status": "ok",
        "message": f"Mock 세션 #{req.session_id} 생성 완료",
        "session_id": req.session_id,
        "f_user_id": 100,
        "m_user_id": 200,
    }


@router.post("/add-record")
async def mock_add_record(
    session_id: int,
    user_id: int,
    round_number: int,
    content: str = "",
    ai_response: str | None = None,
):
    """
    Mock DB에 발화 레코드를 추가한다.
    Spring이 INSERT하는 것을 시뮬레이션.
    - 일반 발화: content만 전달 (ai_response는 AI 서버가 나중에 UPDATE)
    - 사이클 브릿지: content 빈값 + ai_response 전달 (AI 단독 메시지 영속화)
    """
    await mock_supa.insert("mediation_records", {
        "session_id": session_id,
        "user_id": user_id,
        "round_number": round_number,
        "content": content,
        "ai_response": ai_response,
    })

    return {"status": "ok", "message": f"레코드 추가: user={user_id}, round={round_number}"}


@router.get("/get-session")
async def mock_get_session(session_id: int):
    """
    Mock DB의 세션 상태를 조회한다 (테스트 클라이언트 시각화용).
    EFT 신호(detected_signals)·단계·진행도 등 AI 서버가 관리하는 상태를 반환.
    프로덕션 /ai/round-analyze 응답 계약은 건드리지 않기 위한 테스트 전용 경로.
    """
    rows = await mock_supa.get("mediation_sessions", {"session_id": f"eq.{session_id}"})
    if not rows:
        return {"status": "not_found", "session_id": session_id}
    s = rows[0]
    return {
        "status":           "ok",
        "session_id":       session_id,
        "eft_stage":        s.get("eft_stage"),
        "stage_rounds":     s.get("stage_rounds"),
        "stage_progress":   s.get("stage_progress"),
        "detected_signals": s.get("detected_signals") or {"f": {}, "m": {}},
        "cycle_definition": s.get("cycle_definition", ""),
    }


@router.post("/restore-supabase")
async def mock_restore_supabase():
    """활성 클라이언트를 실제 Supabase로 복원."""
    supa_module.restore_real_client()
    mock_supa.reset()
    logger.info("[Mock] supa -> 실제 Supabase로 복원")
    return {"status": "ok", "message": "Supabase 복원 완료"}

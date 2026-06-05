# ============================================================
# schemas/request.py — Spring → Python 요청 스키마 (DB-centric)
#
# AI 서버가 Supabase에서 직접 세션 상태를 조회하므로,
# Spring은 최소한의 정보만 전달한다.
# ============================================================

from pydantic import BaseModel, Field


class RoundAnalyzeRequest(BaseModel):
    """POST /ai/round-analyze"""
    session_id: int = Field(..., description="세션 ID")
    f_reply: str = Field(..., max_length=1000)
    m_reply: str = Field(..., max_length=1000)
    bullet_enabled: bool | None = Field(None, description="총알잡기 오버라이드 (None=서버 기본값)")
    max_refine: int | None = Field(None, description="Self-Refine 오버라이드 (0=비활성, None=서버 기본값)")


class CycleRequest(BaseModel):
    """POST /ai/cycle"""
    session_id: int
    f_explore_answer: str = ""
    m_explore_answer: str = ""


class ReportRequest(BaseModel):
    """POST /ai/report"""
    session_id: int

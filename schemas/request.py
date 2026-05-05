# ============================================================
# schemas/request.py — Spring → Python 요청 스키마 (Stateful)
#
# Spring이 관리하는 것: session_id, 발화 텍스트, 수신 텍스트
# Python이 관리하는 것: 히스토리, EFT 상태, 신호, 단계 등 모두
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional


class SessionCreateRequest(BaseModel):
    """
    POST /counseling/session
    세션 최초 생성. 사전 조사 결과만 포함.
    situation 없음 — 첫 발화에서 자연스럽게 파악.
    """
    session_id:  str   = Field(..., description="Spring이 생성한 세션 UUID")
    f_anxiety:   float = Field(..., ge=1.0, le=7.0)
    f_avoidance: float = Field(..., ge=1.0, le=7.0)
    f_mbti:      str   = Field(default="")
    m_anxiety:   float = Field(..., ge=1.0, le=7.0)
    m_avoidance: float = Field(..., ge=1.0, le=7.0)
    m_mbti:      str   = Field(default="")

    model_name:     Optional[str]   = None
    bullet_enabled: Optional[bool]  = None
    emotion_weight: Optional[float] = None

    class Config:
        json_schema_extra = {
            "example": {
                "session_id":  "550e8400-e29b-41d4-a716-446655440000",
                "f_anxiety":   4.5, "f_avoidance": 2.1, "f_mbti": "ENFP",
                "m_anxiety":   1.8, "m_avoidance": 4.2, "m_mbti": "ISTJ",
            }
        }


class CounselingRoundRequest(BaseModel):
    """
    POST /counseling/round
    매 라운드. session_id + 양측 발화만.
    """
    session_id: str = Field(..., description="세션 UUID")
    f_reply:    str = Field(..., max_length=500)
    m_reply:    str = Field(..., max_length=500)

    class Config:
        json_schema_extra = {
            "example": {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "f_reply": "왜 연락을 안 해줘? 나 무시하는 거야?",
                "m_reply": "바빴어. 왜 그렇게 예민하게 굴어.",
            }
        }


class CycleConsentRequest(BaseModel):
    """POST /counseling/cycle — 사이클 동의"""
    session_id: str
    f_agreed:   bool
    m_agreed:   bool


class ReportRequest(BaseModel):
    """POST /counseling/report — 최종 보고서 (session_id만)"""
    session_id: str


class EndConsentRequest(BaseModel):
    """POST /counseling/end — 3단계 종료 동의"""
    session_id: str
    f_agreed:   bool
    m_agreed:   bool

# ============================================================
# schemas/request.py — Spring → Python 요청 스키마 (Stateless)
#
# Spring이 관리하는 것: 세션 생명주기, DB 영속화, 상태 추적
# Python이 하는 것:     요청마다 분석 → 결과 반환 (상태 미보유)
#
# Spring은 매 요청에 필요한 모든 컨텍스트를 함께 보내야 함.
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional


class RoundAnalyzeRequest(BaseModel):
    """
    POST /ai/round-analyze
    Spring이 양측 발화 + 전체 컨텍스트를 보내면 AI가 분석 후 응답 반환.
    """
    session_id: str = Field(..., description="Spring이 관리하는 세션 UUID")

    # 현재 라운드 입력
    f_reply: str = Field(..., max_length=500)
    m_reply: str = Field(..., max_length=500)

    # 커플 프로필 (ECR-R + MBTI)
    f_anxiety:   float = Field(..., ge=1.0, le=7.0)
    f_avoidance: float = Field(..., ge=1.0, le=7.0)
    f_mbti:      str   = Field(default="")
    m_anxiety:   float = Field(..., ge=1.0, le=7.0)
    m_avoidance: float = Field(..., ge=1.0, le=7.0)
    m_mbti:      str   = Field(default="")

    # 대화 히스토리 (Spring DB에서 꺼내 그대로 전달)
    f_history: list[dict] = Field(default_factory=list, description='[{"role":"user","content":"..."},{"role":"assistant","content":"..."}]')
    m_history: list[dict] = Field(default_factory=list)

    # EFT 상태 (Spring DB에서 꺼내 그대로 전달)
    eft_stage:        int  = Field(default=1, ge=1, le=3)
    round_num:        int  = Field(default=1, ge=1)
    stage_rounds:     dict = Field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    stage_progress:   int  = Field(default=0, ge=0, le=100)
    signals:          Optional[dict] = None
    cycle_definition: str  = ""

    # 설정 오버라이드 (선택)
    model_name:     Optional[str]   = None
    bullet_enabled: Optional[bool]  = None
    emotion_weight: Optional[float] = None

    class Config:
        json_schema_extra = {
            "example": {
                "session_id":  "550e8400-e29b-41d4-a716-446655440000",
                "f_reply": "왜 연락을 안 해줘? 나 무시하는 거야?",
                "m_reply": "바빴어. 왜 그렇게 예민하게 굴어.",
                "f_anxiety": 4.5, "f_avoidance": 2.1, "f_mbti": "ENFP",
                "m_anxiety": 1.8, "m_avoidance": 4.2, "m_mbti": "ISTJ",
                "f_history": [], "m_history": [],
                "eft_stage": 1, "round_num": 1,
            }
        }


class CycleRequest(BaseModel):
    """
    POST /ai/cycle
    사이클 탐색/정의. Spring이 히스토리와 현재 사이클 상태를 전달.
    """
    session_id: str

    # 대화 히스토리
    f_history: list[dict] = Field(default_factory=list)
    m_history: list[dict] = Field(default_factory=list)

    # 현재 사이클 정의 (없으면 탐색 단계, 있으면 정의 단계)
    cycle_definition: str = ""


class ReportRequest(BaseModel):
    """
    POST /ai/report
    최종 보고서 생성. Spring이 전체 히스토리 + 프로필을 전달.
    """
    session_id: str

    # 커플 프로필
    f_anxiety:   float = Field(..., ge=1.0, le=7.0)
    f_avoidance: float = Field(..., ge=1.0, le=7.0)
    f_mbti:      str   = Field(default="")
    m_anxiety:   float = Field(..., ge=1.0, le=7.0)
    m_avoidance: float = Field(..., ge=1.0, le=7.0)
    m_mbti:      str   = Field(default="")

    # 전체 대화 히스토리
    f_history: list[dict] = Field(default_factory=list)
    m_history: list[dict] = Field(default_factory=list)

    # 사이클 정의
    cycle_definition: str = ""

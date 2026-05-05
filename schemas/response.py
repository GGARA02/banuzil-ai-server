# ============================================================
# schemas/response.py — Python → Spring 응답 스키마 (Stateful)
#
# Spring이 받는 것: 텍스트 메시지 + 최소한의 상태 플래그
# Spring이 저장할 것: f_message, m_message (DB 저장용)
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional


class SessionCreateResponse(BaseModel):
    """POST /counseling/session 응답"""
    session_id:     str
    status:         str = "created"
    classification: str  # 32분류 레이블
    ipv_risk_flag:  bool = False
    message:        str = "세션이 생성되었습니다. 상담을 시작하세요."


class CounselingRoundResponse(BaseModel):
    """
    POST /counseling/round 응답.
    Spring은 f_message → 여성에게, m_message → 남성에게 전달.
    나머지 필드는 Spring이 필요한 경우만 사용.
    """
    session_id: str

    # Spring이 내담자에게 전달할 텍스트 (핵심)
    f_message:  str
    m_message:  str

    # Spring이 UI 처리에 필요한 최소 플래그
    eft_stage:              int
    needs_cycle_definition: bool = False  # True면 /counseling/cycle 호출
    risk_flag:              bool = False  # True면 상담 중단 + 전문기관 연계
    risk_category:          str  = ""

    # 디버깅/로깅용 (Spring이 저장해두면 유용)
    stage_progress:    int   = 0
    bullet_detected:   bool  = False
    eval_score:        Optional[float] = None
    neutrality_result: Optional[dict]  = None  # score, bias_direction, passed, regen_triggered


class CycleExploreResponse(BaseModel):
    """POST /counseling/cycle — 탐색 질문 단계"""
    session_id:  str
    f_question:  str
    m_question:  str
    cycle_round: int


class CycleDefinitionResponse(BaseModel):
    """POST /counseling/cycle — 사이클 정의 단계"""
    session_id:       str
    cycle_definition: str
    message:          str = "양측 동의를 기다립니다."


class CycleAgreedResponse(BaseModel):
    """POST /counseling/cycle — 동의 완료"""
    session_id:  str
    status:      str = "cycle_agreed"
    next_stage:  int = 2
    message:     str = "사이클 동의 완료. 2단계로 진입합니다."


class CounselingReportResponse(BaseModel):
    """POST /counseling/report 응답"""
    session_id: str
    f_report:   str  # 여성 내담자 전용 보고서 텍스트
    m_report:   str  # 남성 내담자 전용 보고서 텍스트

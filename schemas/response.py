# ============================================================
# schemas/response.py — Python → Spring 응답 스키마 (Stateless)
#
# AI 서버는 분석 결과 + 상태 변경 힌트를 반환.
# Spring이 이 결과를 받아 DB에 저장하고 상태를 업데이트함.
# ============================================================

from pydantic import BaseModel, Field
from typing import Optional


class RoundAnalyzeResponse(BaseModel):
    """
    POST /ai/round-analyze 응답.
    Spring은 이 결과를 받아서:
    1. f_message/m_message → 각 내담자에게 전달
    2. updated_* 필드들 → DB에 저장 (다음 라운드 요청에 재전달)
    """
    session_id: str

    # 내담자에게 전달할 응답 텍스트
    f_message: str
    m_message: str

    # Spring이 DB에 저장해야 할 업데이트된 상태
    updated_eft_stage:    int
    updated_stage_rounds: dict
    updated_stage_progress: int
    updated_signals:      dict
    updated_f_history:    list[dict]
    updated_m_history:    list[dict]

    # Spring UI 처리용 플래그
    needs_cycle_definition: bool = False
    risk_flag:              bool = False
    risk_category:          str  = ""

    # 디버깅/로깅용
    bullet_detected:   bool            = False
    bullet_type:       str             = "None"
    eval_score:        Optional[float] = None
    eval_scores:       Optional[dict]  = None
    neutrality_result: Optional[dict]  = None
    f_emotion:         Optional[dict]  = None
    m_emotion:         Optional[dict]  = None
    risk_keywords:     list            = []


class CycleExploreResponse(BaseModel):
    """사이클 탐색 질문"""
    session_id:  str
    f_question:  str
    m_question:  str
    cycle_round: int


class CycleDefinitionResponse(BaseModel):
    """사이클 정의 결과"""
    session_id:       str
    cycle_definition: str
    message:          str = "양측 동의를 기다립니다."


class ReportResponse(BaseModel):
    """최종 보고서"""
    session_id: str
    f_report:   str
    m_report:   str

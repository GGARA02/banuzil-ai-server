# ============================================================
# schemas/response.py — Python → Spring 응답 스키마 (DB-centric)
#
# AI 서버가 DB에 직접 저장하므로, Spring에는 최소한의 결과만 반환.
# ============================================================

from pydantic import BaseModel


class RoundAnalyzeResponse(BaseModel):
    """POST /ai/round-analyze 응답"""
    session_id: int
    f_message: str
    m_message: str
    needs_cycle_definition: bool = False
    risk_flag: bool = False
    # 진행 상태 가시화용 (Spring은 무시 가능, 테스트/디버그·UI 표시용)
    eft_stage: int = 1
    stage_progress: int = 0


class CycleExploreResponse(BaseModel):
    """사이클 탐색 질문"""
    session_id: int
    f_question: str
    m_question: str


class CycleDefinitionResponse(BaseModel):
    """사이클 정의 결과"""
    session_id: int
    cycle_definition: str
    # 사이클 정의 직후 상담사가 내담자에게 전달할 브릿지 메시지 (2단계로 연결)
    # 스프링은 이 값을 mediation_records.ai_response로 INSERT (content 빈값, 직전 완료 라운드)
    f_message: str = ""
    m_message: str = ""


class ReportSections(BaseModel):
    """보고서 4개 섹션"""
    emotion_summary: str
    partner_understanding: str
    mediation_plans: str
    recommended_dialogues: str


class ReportResponse(BaseModel):
    """최종 보고서"""
    session_id: int
    f_report: ReportSections
    m_report: ReportSections

# ============================================================
# services/rag/embedding_service.py
#
# 세션 종료 시 사이클 정의를 벡터화하여 session_embeddings에 저장.
# - 검색 키:    사이클 정의 벡터 (embedding)
# - 주입 내용:  보고서 전문 (summary_text)
# 사이클 정의가 없는 세션은 임베딩하지 않는다.
# ============================================================

import logging
from openai import AsyncOpenAI
from config.settings import EMBEDDING_MODEL, OPENAI_API_KEY
from services.supabase_client import supa

logger = logging.getLogger(__name__)

_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)


def make_couple_key(user_id_a: int, user_id_b: int) -> str:
    """두 유저 ID → 정렬된 커플 키 (누가 initiator든 동일)"""
    return f"{min(user_id_a, user_id_b)}_{max(user_id_a, user_id_b)}"


async def embed_text(text: str) -> list[float]:
    """OpenAI text-embedding-3-small 호출 → 1536차원 벡터"""
    resp = await _openai.embeddings.create(input=text, model=EMBEDDING_MODEL)
    return resp.data[0].embedding


def build_session_summary(cycle_definition: str, f_report: dict, m_report: dict) -> str:
    """보고서 전문 + 사이클 정의 이어붙이기. 검색 히트 시 프롬프트에 주입할 내용."""
    return f"""[사이클 정의]
{cycle_definition or '사이클 미정의'}

[여성 — 감정 정리]
{f_report.get('emotion_summary', '')}

[여성 — 파트너 이해]
{f_report.get('partner_understanding', '')}

[여성 — 중재안]
{f_report.get('mediation_plans', '')}

[여성 — 추천 대화법]
{f_report.get('recommended_dialogues', '')}

[남성 — 감정 정리]
{m_report.get('emotion_summary', '')}

[남성 — 파트너 이해]
{m_report.get('partner_understanding', '')}

[남성 — 중재안]
{m_report.get('mediation_plans', '')}

[남성 — 추천 대화법]
{m_report.get('recommended_dialogues', '')}"""


async def save_session_embedding(
    session_id: int,
    f_user_id: int,
    m_user_id: int,
    cycle_definition: str,
    f_report: dict,
    m_report: dict,
    eft_stage: int,
) -> None:
    """
    세션 완료 시 호출.
    사이클 정의가 없으면 임베딩하지 않고 종료한다.
    """
    if not cycle_definition or not cycle_definition.strip():
        logger.info(f"[{session_id}] 사이클 정의 없음 → 임베딩 스킵")
        return

    summary = build_session_summary(cycle_definition, f_report, m_report)
    vector  = await embed_text(cycle_definition)   # 사이클 정의만 벡터화

    await supa.insert("session_embeddings", {
        "session_id":      session_id,
        "couple_key":      make_couple_key(f_user_id, m_user_id),
        "eft_final_stage": eft_stage,
        "cycle_text":      cycle_definition,
        "summary_text":    summary,
        "embedding":       str(vector),   # pgvector는 "[...]" 텍스트 형식 입력
    })
    logger.info(f"[{session_id}] 세션 임베딩 저장 완료 (couple={make_couple_key(f_user_id, m_user_id)})")

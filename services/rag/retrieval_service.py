# ============================================================
# services/rag/retrieval_service.py
#
# 사이클 정의 생성 직후 호출.
# 현재 사이클 정의를 벡터화하여 동일 커플의 과거 세션 중
# 유사도 기준 이상 + 최고 1건을 검색한다.
# ============================================================

import logging
from services.rag.embedding_service import embed_text, make_couple_key
from services.supabase_client import supa
from config.settings import RAG_SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)


async def find_best_past_session(
    f_user_id: int,
    m_user_id: int,
    current_session_id: int,
    cycle_definition: str,
) -> dict | None:
    """
    현재 사이클 정의와 가장 유사한 과거 세션 1건 반환.
    유사도가 기준(RAG_SIMILARITY_THRESHOLD) 미만이면 None.
    """
    if not cycle_definition or not cycle_definition.strip():
        return None

    couple_key = make_couple_key(f_user_id, m_user_id)
    query_vec  = await embed_text(cycle_definition)

    results = await supa.rpc("match_best_couple_session", {
        "query_embedding":      str(query_vec),   # pgvector "[...]" 텍스트 형식
        "target_couple_key":    couple_key,
        "exclude_session_id":   current_session_id,
        "similarity_threshold": RAG_SIMILARITY_THRESHOLD,
    })

    best = results[0] if results else None
    if best:
        logger.info(
            f"[{current_session_id}] RAG 히트: 과거 세션 {best.get('session_id')} "
            f"유사도={best.get('similarity'):.3f}"
        )
    else:
        logger.info(f"[{current_session_id}] RAG 미스: 기준 이상 과거 세션 없음")
    return best

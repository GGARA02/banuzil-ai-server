# ============================================================
# scripts/backfill_embeddings.py
#
# 기존 완료 세션 중 사이클 정의가 있는데 임베딩이 없는 세션을
# 일괄 임베딩하여 session_embeddings에 저장한다.
#
# 실행: python -m scripts.backfill_embeddings
# ============================================================

import asyncio
import logging

from services.supabase_client import supa
from services.rag.embedding_service import save_session_embedding

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill")


async def _fetch_reports(session_id: int) -> dict:
    """세션의 보고서 2건을 user_id → 섹션 dict로 반환."""
    reports = await supa.get("mediation_reports", {"session_id": f"eq.{session_id}"})
    by_user = {}
    for r in reports:
        by_user[r["user_id"]] = {
            "emotion_summary":       r.get("emotion_summary", ""),
            "partner_understanding": r.get("partner_understanding", ""),
            "mediation_plans":       r.get("mediation_plans", ""),
            "recommended_dialogues": r.get("recommended_dialogues", ""),
        }
    return by_user


async def backfill() -> None:
    # 1. 세션 전체 조회 (사이클 정의 유무는 아래 Python에서 필터)
    sessions = await supa.get("mediation_sessions", {
        "select": "session_id,initiator_id,participant_id,eft_stage,cycle_definition",
    })

    # 2. 이미 임베딩된 세션 ID 집합
    existing = await supa.get("session_embeddings", {"select": "session_id"})
    done_ids = {e["session_id"] for e in existing}

    targets = [s for s in sessions if s["session_id"] not in done_ids and (s.get("cycle_definition") or "").strip()]
    logger.info(f"백필 대상 세션: {len(targets)}건")

    for s in targets:
        sid = s["session_id"]
        try:
            # 유저 성별 판별
            ini, par = s["initiator_id"], s["participant_id"]
            if not par:
                logger.warning(f"[{sid}] 참가자 없음 → 스킵")
                continue
            users = await supa.get("users", {"user_id": f"in.({ini},{par})"})
            umap = {u["user_id"]: u for u in users}
            if umap.get(ini, {}).get("gender") == "female":
                f_id, m_id = ini, par
            else:
                f_id, m_id = par, ini

            reports = await _fetch_reports(sid)
            f_report = reports.get(f_id, {})
            m_report = reports.get(m_id, {})

            await save_session_embedding(
                session_id       = sid,
                f_user_id        = f_id,
                m_user_id        = m_id,
                cycle_definition = s["cycle_definition"],
                f_report         = f_report,
                m_report         = m_report,
                eft_stage        = s.get("eft_stage", 3),
            )
        except Exception as e:
            logger.error(f"[{sid}] 백필 실패: {e}")

    logger.info("백필 완료")


if __name__ == "__main__":
    asyncio.run(backfill())

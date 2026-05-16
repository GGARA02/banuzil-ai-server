# ============================================================
# services/session_service.py — Supabase DB 읽기/쓰기
#
# AI 서버가 DB에서 직접 세션 컨텍스트를 조회하고,
# 라운드 결과 및 보고서를 저장한다.
# ============================================================

import logging
from services.supabase_client import supa
from services.attachment_service import build_couple_profile
from config.prompts.stage_prompts import SignalState

logger = logging.getLogger(__name__)


def _signals_from_dict(d: dict | None) -> SignalState:
    s = SignalState()
    if not d:
        return s
    if "f" in d:
        s.f.update(d["f"])
    if "m" in d:
        s.m.update(d["m"])
    return s


def _signals_to_dict(signals: SignalState) -> dict:
    return {"f": dict(signals.f), "m": dict(signals.m)}


async def fetch_session_context(session_id: int) -> dict:
    """
    라운드 시작 전 호출.
    DB에서 세션 상태, 유저 프로필, 히스토리를 조회하여
    그래프 실행에 필요한 컨텍스트를 반환한다.
    """
    # 1. 세션 조회
    sessions = await supa.get("mediation_sessions", {
        "session_id": f"eq.{session_id}",
    })
    if not sessions:
        raise ValueError(f"세션을 찾을 수 없습니다: {session_id}")
    session = sessions[0]

    initiator_id = session["initiator_id"]
    participant_id = session["participant_id"]
    if not participant_id:
        raise ValueError(f"참가자가 아직 없는 세션입니다: {session_id}")

    # 2. 유저 정보 조회 (initiator + participant)
    users = await supa.get("users", {
        "user_id": f"in.({initiator_id},{participant_id})",
    })
    user_map = {u["user_id"]: u for u in users}
    initiator = user_map[initiator_id]
    participant = user_map[participant_id]

    # 3. gender로 f/m 판별
    if initiator["gender"] == "female":
        f_user, m_user = initiator, participant
    else:
        f_user, m_user = participant, initiator

    # 4. 애착 점수 조회
    attachments = await supa.get("user_attachments", {
        "user_id": f"in.({f_user['user_id']},{m_user['user_id']})",
    })
    attach_map = {a["user_id"]: a for a in attachments}

    f_attach = attach_map.get(f_user["user_id"], {})
    m_attach = attach_map.get(m_user["user_id"], {})

    # 5. 커플 프로필 생성
    couple_profile = build_couple_profile(
        session_id  = str(session_id),
        f_anxiety   = f_attach.get("anxiety_score", 3.5),
        f_avoidance = f_attach.get("avoidance_score", 3.5),
        f_mbti      = f_user.get("mbti") or "",
        f_situation = "",
        m_anxiety   = m_attach.get("anxiety_score", 3.5),
        m_avoidance = m_attach.get("avoidance_score", 3.5),
        m_mbti      = m_user.get("mbti") or "",
        m_situation = "",
    )

    # 6. 히스토리 재조립
    records = await supa.get("mediation_records", {
        "session_id": f"eq.{session_id}",
        "order": "round_number.asc,created_at.asc",
    })

    f_history = []
    m_history = []
    for r in records:
        uid = r["user_id"]
        if uid == f_user["user_id"]:
            target = f_history
        elif uid == m_user["user_id"]:
            target = m_history
        else:
            continue

        if r.get("content"):
            target.append({"role": "user", "content": r["content"]})
        if r.get("ai_response"):
            target.append({"role": "assistant", "content": r["ai_response"]})

    # 7. EFT 상태
    signals = _signals_from_dict(session.get("detected_signals"))

    return {
        "session": session,
        "couple_profile": couple_profile,
        "f_user_id": f_user["user_id"],
        "m_user_id": m_user["user_id"],
        "f_history": f_history,
        "m_history": m_history,
        "eft_stage": session.get("eft_stage", 1),
        "stage_rounds": session.get("stage_rounds", {1: 0, 2: 0, 3: 0}),
        "stage_progress": session.get("stage_progress", 0),
        "signals": signals,
        "cycle_definition": session.get("cycle_definition", ""),
        "cycle_skip_until": session.get("cycle_skip_until", 0),
        "round_num": session.get("current_round", 1),
    }


async def save_round_result(session_id: int, context: dict, result: dict) -> None:
    """
    라운드 완료 후 호출.
    - mediation_records: f, m 각각의 ai_response UPDATE
    - mediation_sessions: EFT 상태 UPDATE
    """
    round_num = context["round_num"]
    f_user_id = context["f_user_id"]
    m_user_id = context["m_user_id"]

    f_response = result.get("f_response", "")
    m_response = result.get("m_response", "")

    # 1. ai_response UPDATE (해당 라운드의 각 유저 record)
    await supa.update(
        "mediation_records",
        {"session_id": f"eq.{session_id}", "round_number": f"eq.{round_num}", "user_id": f"eq.{f_user_id}"},
        {"ai_response": f_response},
    )
    await supa.update(
        "mediation_records",
        {"session_id": f"eq.{session_id}", "round_number": f"eq.{round_num}", "user_id": f"eq.{m_user_id}"},
        {"ai_response": m_response},
    )

    # 2. mediation_sessions EFT 상태 UPDATE
    signals = result.get("signals")
    signals_dict = _signals_to_dict(signals) if hasattr(signals, "f") else signals or {}

    await supa.update(
        "mediation_sessions",
        {"session_id": f"eq.{session_id}"},
        {
            "current_round": round_num + 1,
            "eft_stage": result.get("eft_stage", context["eft_stage"]),
            "stage_rounds": result.get("stage_rounds", context["stage_rounds"]),
            "stage_progress": result.get("stage_progress", context["stage_progress"]),
            "detected_signals": signals_dict,
            "cycle_definition": result.get("cycle_definition", context["cycle_definition"]),
            "cycle_skip_until": result.get("cycle_skip_until", context["cycle_skip_until"]),
        },
    )

    # 3. 디버그 로그
    eval_scores = result.get("eval_scores") or {}
    nr = result.get("neutrality_result") or {}
    logger.info(
        f"[{session_id}] R{round_num} "
        f"stage={result.get('eft_stage')} | "
        f"eval={eval_scores.get('weighted_avg')} | "
        f"neutrality={nr.get('passed', True)} | "
        f"bullet={result.get('bullet_type', 'None')} | "
        f"f_emotion={result.get('f_emotion_data')} | "
        f"m_emotion={result.get('m_emotion_data')}"
    )


async def save_cycle_definition(session_id: int, cycle_definition: str) -> None:
    """사이클 정의 저장."""
    await supa.update(
        "mediation_sessions",
        {"session_id": f"eq.{session_id}"},
        {"cycle_definition": cycle_definition},
    )


async def save_report(session_id: int, f_user_id: int, m_user_id: int, f_report: dict, m_report: dict) -> None:
    """보고서 저장 (4개 섹션 × 2명)."""
    await supa.insert("mediation_reports", [
        {
            "session_id": session_id,
            "user_id": f_user_id,
            "emotion_summary": f_report["emotion_summary"],
            "partner_understanding": f_report["partner_understanding"],
            "mediation_plans": f_report["mediation_plans"],
            "recommended_dialogues": f_report["recommended_dialogues"],
        },
        {
            "session_id": session_id,
            "user_id": m_user_id,
            "emotion_summary": m_report["emotion_summary"],
            "partner_understanding": m_report["partner_understanding"],
            "mediation_plans": m_report["mediation_plans"],
            "recommended_dialogues": m_report["recommended_dialogues"],
        },
    ])

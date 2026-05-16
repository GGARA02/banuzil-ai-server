# ============================================================
# services/report_service.py — 최종 보고서 생성 (4개 섹션 분리)
# ============================================================

import asyncio
import json
import re
import logging
from config.attachment_weight import CoupleProfile
from config.prompts.eft_base import build_system_prompt, build_report_prompt
from services.llm import call_llm_with_history
from config.settings import MODEL_NAME, REPORT_MAX_TOKENS

logger = logging.getLogger(__name__)

SECTION_KEYS = ["emotion_summary", "partner_understanding", "mediation_plans", "recommended_dialogues"]


def _parse_report_json(raw: str) -> dict:
    """GPT 응답에서 JSON 추출 및 파싱."""
    # ```json ... ``` 블록 추출
    match = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
    text = match.group(1) if match else raw.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # JSON 파싱 실패 시 전체 텍스트를 emotion_summary에 넣고 나머지 빈 문자열
        logger.warning("보고서 JSON 파싱 실패, 원본 텍스트를 emotion_summary에 저장")
        return {k: "" for k in SECTION_KEYS} | {"emotion_summary": raw.strip()}

    # 키 누락 방어
    return {k: parsed.get(k, "") for k in SECTION_KEYS}


async def generate_report(
    couple_profile:   CoupleProfile,
    f_history:        list[dict],
    m_history:        list[dict],
    cycle_definition: str = "",
) -> tuple[dict, dict]:
    """
    여성·남성 최종 보고서 병렬 생성.
    Returns: (f_report_sections, m_report_sections)
    """
    def _fmt_history(history: list[dict]) -> str:
        return "\n\n".join(
            f"{'내담자' if h['role']=='user' else 'AI 상담사'}: {h['content']}"
            for h in history
        )

    f_hist_text = _fmt_history(f_history)
    m_hist_text = _fmt_history(m_history)

    cycle_note = f"\n[두 사람의 부정적 상호작용 사이클]\n{cycle_definition}\n" if cycle_definition else ""

    f_sys = build_system_prompt(
        is_female=True, couple_profile=couple_profile,
        eft_stage=3, stage_progress=100,
    )
    m_sys = build_system_prompt(
        is_female=False, couple_profile=couple_profile,
        eft_stage=3, stage_progress=100,
    )

    f_prompt = build_report_prompt(True,  cycle_note + f_hist_text)
    m_prompt = build_report_prompt(False, cycle_note + m_hist_text)

    f_raw, m_raw = await asyncio.gather(
        call_llm_with_history(f_sys, [], f_prompt, model=MODEL_NAME, max_tokens=REPORT_MAX_TOKENS),
        call_llm_with_history(m_sys, [], m_prompt, model=MODEL_NAME, max_tokens=REPORT_MAX_TOKENS),
    )

    return _parse_report_json(f_raw), _parse_report_json(m_raw)

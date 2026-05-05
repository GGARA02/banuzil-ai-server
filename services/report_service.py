# ============================================================
# services/report_service.py — 최종 보고서 생성
# ============================================================

import asyncio
from config.attachment_weight import CoupleProfile
from config.prompts.eft_base import build_system_prompt, build_report_prompt
from services.llm import call_llm_with_history
from config.settings import MODEL_NAME, REPORT_MAX_TOKENS


async def generate_report(
    couple_profile:   CoupleProfile,
    f_history:        list[dict],
    m_history:        list[dict],
    cycle_definition: str = "",
) -> tuple[str, str]:
    """
    여성·남성 최종 보고서 병렬 생성.
    Returns: (f_report, m_report)
    """
    def _fmt_history(history: list[dict], gender: str) -> str:
        return "\n\n".join(
            f"{'내담자' if h['role']=='user' else 'AI 상담사'}: {h['content']}"
            for h in history
        )

    f_hist_text = _fmt_history(f_history, "여성")
    m_hist_text = _fmt_history(m_history, "남성")

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

    f_report, m_report = await asyncio.gather(
        call_llm_with_history(f_sys, [], f_prompt, model=MODEL_NAME, max_tokens=REPORT_MAX_TOKENS),
        call_llm_with_history(m_sys, [], m_prompt, model=MODEL_NAME, max_tokens=REPORT_MAX_TOKENS),
    )

    return f_report, m_report

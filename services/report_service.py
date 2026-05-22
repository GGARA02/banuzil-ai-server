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


def _sanitize_json_text(text: str) -> str:
    """JSON 문자열 값 안의 이스케이프 안 된 줄바꿈을 \\n으로 교체."""
    return text.replace('\r\n', '\\n').replace('\r', '\\n').replace('\n', '\\n')


def _try_parse(text: str) -> dict | None:
    """
    JSON 파싱 시도. 성공하면 섹션 dict, 실패하면 None.
    1차: 원본 그대로 (정상적인 여러 줄 JSON은 그대로 파싱됨)
    2차: 문자열 값 안에 escape 안 된 raw 줄바꿈이 있을 때만 정제 후 재시도
    """
    for candidate in (text, _sanitize_json_text(text)):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return {k: parsed.get(k, "") for k in SECTION_KEYS}
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _fallback_section_split(raw: str) -> dict:
    """
    JSON 파싱 전부 실패 시 키워드로 섹션 분리 시도.
    그것도 실패하면 raw 텍스트를 emotion_summary에만 저장.
    """
    result = {k: "" for k in SECTION_KEYS}

    # 섹션 키 또는 한국어 제목으로 분리 시도
    patterns = {
        "emotion_summary":       r"(?:emotion_summary|나의 생각과 감정)[^\n]*\n(.*?)(?=(?:partner_understanding|파트너의 입장|mediation_plans|중재안|recommended_dialogues|추천 대화)|$)",
        "partner_understanding": r"(?:partner_understanding|파트너의 입장)[^\n]*\n(.*?)(?=(?:mediation_plans|중재안|recommended_dialogues|추천 대화)|$)",
        "mediation_plans":       r"(?:mediation_plans|중재안)[^\n]*\n(.*?)(?=(?:recommended_dialogues|추천 대화)|$)",
        "recommended_dialogues": r"(?:recommended_dialogues|추천 대화)[^\n]*\n(.*?)$",
    }

    matched_any = False
    for key, pattern in patterns.items():
        m = re.search(pattern, raw, re.DOTALL | re.IGNORECASE)
        if m:
            result[key] = m.group(1).strip()
            matched_any = True

    if not matched_any:
        # 섹션 분리도 실패 → 전체 텍스트를 emotion_summary에만 저장
        logger.warning("보고서 섹션 분리 실패, 전체 텍스트를 emotion_summary에 저장")
        result["emotion_summary"] = raw.strip()

    return result


def _parse_report_json(raw: str) -> dict:
    """GPT 응답에서 JSON 추출 및 파싱. 3단계 시도."""

    # 1차 시도: ```json 또는 ``` 코드블록 추출
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        result = _try_parse(match.group(1))
        if result is not None:
            return result
        logger.debug("코드블록 추출 후 JSON 파싱 실패, 2차 시도")

    # 2차 시도: { } 중괄호 직접 추출
    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        result = _try_parse(brace_match.group(0))
        if result is not None:
            return result
        logger.debug("중괄호 추출 후 JSON 파싱 실패, fallback 진입")

    # 3차: raw 전체로 파싱 시도
    result = _try_parse(raw.strip())
    if result is not None:
        return result

    # 최종 fallback: 키워드 기반 섹션 분리
    logger.warning("보고서 JSON 파싱 전 단계 실패, 키워드 fallback 진입")
    return _fallback_section_split(raw)


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

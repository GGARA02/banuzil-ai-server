# ============================================================
# services/rag/rag_prompt_builder.py
#
# 검색된 과거 세션 1건(보고서 전문) → 시스템 프롬프트 삽입 텍스트.
# ============================================================


def build_rag_context(best_session: dict | None) -> str:
    """검색된 최고 유사도 과거 세션 1건의 보고서 → 프롬프트 삽입 텍스트"""
    if not best_session:
        return ""

    similarity = best_session.get("similarity", 0)
    text       = best_session.get("summary_text", "")
    if not text:
        return ""

    return (
        f"\n\n[이 커플의 과거 상담 기록 — 유사도 {similarity:.0%} — 내부 참고용]\n"
        f"{text}\n\n"
        "[과거 기록 활용 규칙]\n"
        "- 과거 세션에서 반복된 패턴(사이클)과 효과적이었던 접근 방식을 참고하라.\n"
        "- 과거 상담 내용을 내담자에게 직접 인용하거나 언급하지 마라.\n"
        "- 현재 내담자의 실제 발화와 현재 갈등 상황을 항상 최우선으로 반영하라.\n"
    )

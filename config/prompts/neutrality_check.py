# ============================================================
# config/prompts/neutrality_check.py
# 중립 검사 AI 채점 프롬프트
# DSPy 최적화 파일 없을 때 기본 프롬프트로 사용됨
# ============================================================

from config.attachment_weight import CoupleProfile


def build_neutrality_prompt(
    f_reply:     str,
    m_reply:     str,
    f_response:  str,
    m_response:  str,
    couple_profile: CoupleProfile,
    eft_stage:   int,
) -> str:
    """
    중립 검사 AI 채점 프롬프트.
    이번 라운드 발화 + 응답만 입력 (히스토리 없음 — 토큰 절약 + 채점 집중도).
    """
    f_type = couple_profile.f_profile.attach_type
    m_type = couple_profile.m_profile.attach_type
    pattern = couple_profile.classification

    stage_names = {
        1: "1단계 De-escalation (갈등 완화)",
        2: "2단계 Restructuring (상호작용 재구성)",
        3: "3단계 Consolidation (통합 공고화)",
    }
    stage_label = stage_names.get(eft_stage, f"{eft_stage}단계")

    return f"""당신은 커플 상담 AI의 응답이 양측에 공평한지 평가하는 독립적 검증자입니다.
상담사가 아닌 심판 역할입니다. 절대로 상담 조언을 추가하지 마세요.

[중요 맥락]
상담 AI는 여성과 남성에게 각각 별도의 메시지를 보냅니다.
두 메시지의 내용이 서로 다른 것은 구조적으로 정상입니다.
평가 기준은 단순 내용 비교가 아니라,
각 메시지 안에서 상대방(파트너)을 어떻게 묘사했는지의 공정성입니다.

[평가 맥락]
여성 애착유형: {f_type}
남성 애착유형: {m_type}
커플 결합 패턴: {pattern}
현재 EFT 단계: {stage_label}

[이번 라운드 입력]
여성 발화: {f_reply}
남성 발화: {m_reply}

[상담 AI 생성 응답]
여성에게 보내는 메시지:
{f_response}

남성에게 보내는 메시지:
{m_response}

[평가 기준 4가지]
1. 언급 균형: 양측 발화 내용이 비슷한 비중으로 다뤄졌는가
2. 정서 단어 분포: 한쪽 메시지에만 공감·긍정 표현이 집중됐는가
3. 단정 표현: "당신이 옳다", "상대가 잘못했다" 류 표현이 있는가
4. 인과 귀속: 갈등 원인이 한쪽 사람에게만 귀속됐는가

[주의사항]
- EFT 상담의 특성상 추구형(불안형)에게 더 많은 공감 표현이 들어가는 것은 정상.
  단, 그로 인해 철회형(거부회피형)이 악인으로 묘사되면 편향.
- 각 메시지에서 상대방(파트너)에 대한 서술이 공정한지가 핵심 기준.

[출력 형식 — 순수 JSON만, 다른 텍스트 없이]
{{
  "neutrality_score": <1~5 정수. 5=완전 중립, 1=심각한 편향>,
  "bias_direction": "toward_f" | "toward_m" | "none",
  "violations": ["위반 항목 설명", "..."],
  "reasoning": "채점 근거 2~3문장",
  "feedback_for_regen": "재생성 시 상담 AI에게 전달할 교정 지침. pass면 빈 문자열"
}}"""

# ============================================================
# config/attachment_weight.py
# ECR-R 점수 → 애착 가중치 변환 + 애착 유형 분류 + 32결합 분류
# ============================================================

from dataclasses import dataclass, field
from config.settings import ANXIETY_CUTOFF, AVOIDANCE_CUTOFF, TIER_RANGE_OFFSET


# ── 애착 유형 상수 ─────────────────────────────────────────
SECURE            = "안정형"
ANXIOUS           = "불안형(몰입형)"
DISMISSING        = "거부회피형"
FEARFUL           = "공포회피형(혼란형)"

# 공포회피형 포함 결합 → IPV 위험 플래그
IPV_RISK_COMBOS = {
    (FEARFUL, ANXIOUS), (FEARFUL, DISMISSING),
    (ANXIOUS, FEARFUL), (DISMISSING, FEARFUL),
    (FEARFUL, FEARFUL), (FEARFUL, SECURE), (SECURE, FEARFUL),
}


@dataclass
class AttachmentProfile:
    """한 내담자의 애착 프로파일"""
    anxiety_score:    float
    avoidance_score:  float
    attach_type:      str
    anxiety_weight:   float   # 0~1
    avoidance_weight: float   # 0~1
    intervention_tier: str    # LOW / MID / HIGH


@dataclass
class CoupleProfile:
    """커플 전체 프로파일 + 32분류 결과"""
    session_id:       str
    f_profile:        AttachmentProfile
    m_profile:        AttachmentProfile
    f_mbti:           str
    m_mbti:           str
    f_situation:      str
    m_situation:      str
    couple_combo:     str          # "불안형-거부회피형" 등
    classification:   str          # 결합 레이블
    classification_detail: str     # 결합 공통 맥락 설명
    f_classification_detail: str   # 여성 전용 32분류 설명 (여성 프롬프트에 삽입)
    m_classification_detail: str   # 남성 전용 32분류 설명 (남성 프롬프트에 삽입)
    intervention_guide: str        # 개입 강도 지침
    ipv_risk_flag:    bool = False


def _score_to_weight(score: float, cutoff: float) -> float:
    """ECR-R 점수 → 0~1 강도 변수"""
    if score < cutoff:
        # Low tier: 0.00 ~ 0.35
        return round((score - 1.0) / (cutoff - 1.0) * 0.35, 3)
    elif score < cutoff + TIER_RANGE_OFFSET:
        # Mid tier: 0.35 ~ 0.70
        return round(0.35 + (score - cutoff) / TIER_RANGE_OFFSET * 0.35, 3)
    else:
        # High tier: 0.70 ~ 1.00
        max_score = 7.0
        return round(0.70 + (score - (cutoff + TIER_RANGE_OFFSET)) /
                     (max_score - cutoff - TIER_RANGE_OFFSET) * 0.30, 3)


def _weight_to_tier(weight: float) -> str:
    if weight < 0.35:
        return "LOW"
    elif weight < 0.70:
        return "MID"
    return "HIGH"


def classify_attachment_type(anxiety: float, avoidance: float) -> str:
    """ECR-R 점수 → 4가지 애착 유형"""
    high_a  = anxiety   >= ANXIETY_CUTOFF
    high_av = avoidance >= AVOIDANCE_CUTOFF
    if high_a and high_av:
        return FEARFUL
    elif high_a:
        return ANXIOUS
    elif high_av:
        return DISMISSING
    else:
        return SECURE


def build_attachment_profile(anxiety: float, avoidance: float) -> AttachmentProfile:
    attach_type      = classify_attachment_type(anxiety, avoidance)
    anxiety_weight   = _score_to_weight(anxiety,   ANXIETY_CUTOFF)
    avoidance_weight = _score_to_weight(avoidance, AVOIDANCE_CUTOFF)
    max_w            = max(anxiety_weight, avoidance_weight)
    tier             = _weight_to_tier(max_w)
    return AttachmentProfile(
        anxiety_score    = anxiety,
        avoidance_score  = avoidance,
        attach_type      = attach_type,
        anxiety_weight   = anxiety_weight,
        avoidance_weight = avoidance_weight,
        intervention_tier= tier,
    )


# ── 개입 강도 지침 빌더 ─────────────────────────────────────
_ANXIETY_TEXT = {
    0: "[여성/남성 불안 차원 — LOW]\n경미한 불안. 일반 EFT 개입으로 충분. 애착 확인 욕구를 부드럽게 다루되 과도한 개입 불필요.",
    1: "[여성/남성 불안 차원 — MID]\n중등도 불안. 버림받음 공포를 명시적으로 다뤄라. 비난이 실제로는 '연결 요청'임을 반복적으로 재구성해 주어라.",
    2: "[여성/남성 불안 차원 — HIGH]\n고위험 불안. 신경계 안정화 최우선. 압박·직면 금지. 매우 느리고 부드러운 속도로 1차 정서 탐색. 공포를 직접 명명할 준비가 될 때까지 기다려라.",
}
_AVOIDANCE_TEXT = {
    0: "[여성/남성 회피 차원 — LOW]\n경미한 회피. 일반 EFT 개입으로 충분.",
    1: "[여성/남성 회피 차원 — MID]\n중등도 회피. 철회 행동을 '통제 상실 두려움'으로 명시적으로 재구성. 속도를 늦추고 압박 없이 공간을 제공하라.",
    2: "[여성/남성 회피 차원 — HIGH]\n고위험 회피. 철회를 절대 압박하지 마라. '동굴에 들어가는 것이 이 관계를 지키려는 생존 전략'임을 먼저 인정하라. 작은 감정 표현도 즉각 포착해 확대 강화하라.",
}


def build_intervention_guide(f_profile: AttachmentProfile, m_profile: AttachmentProfile) -> str:
    """ECR-R 점수 기반 개입 강도 지침 텍스트 생성"""
    def _tier_idx(tier): return {"LOW": 0, "MID": 1, "HIGH": 2}[tier]

    dims = [
        ("여성 불안",  f_profile.anxiety_weight,   f_profile.anxiety_score,   f_profile.intervention_tier),
        ("여성 회피",  f_profile.avoidance_weight,  f_profile.avoidance_score, f_profile.intervention_tier),
        ("남성 불안",  m_profile.anxiety_weight,    m_profile.anxiety_score,   m_profile.intervention_tier),
        ("남성 회피",  m_profile.avoidance_weight,  m_profile.avoidance_score, m_profile.intervention_tier),
    ]
    dominant = max(dims, key=lambda x: x[1])

    fa_t  = _tier_idx(_weight_to_tier(f_profile.anxiety_weight))
    fav_t = _tier_idx(_weight_to_tier(f_profile.avoidance_weight))
    ma_t  = _tier_idx(_weight_to_tier(m_profile.anxiety_weight))
    mav_t = _tier_idx(_weight_to_tier(m_profile.avoidance_weight))

    lines = [
        f"[개입 강도 지침 — ECR-R 점수 기반]",
        f"여성 불안 {f_profile.anxiety_score:.2f}점 / 여성 회피 {f_profile.avoidance_score:.2f}점",
        f"남성 불안 {m_profile.anxiety_score:.2f}점 / 남성 회피 {m_profile.avoidance_score:.2f}점",
        f"",
        f"▶ 가장 극단적 차원: {dominant[0]} {dominant[2]:.2f}점 → 이 차원 개입에 가장 큰 가중치를 두어라.",
        f"",
        _ANXIETY_TEXT[fa_t].replace("[여성/남성 불안", "[여성 불안"),
        f"",
        _AVOIDANCE_TEXT[fav_t].replace("[여성/남성 회피", "[여성 회피"),
        f"",
        _ANXIETY_TEXT[ma_t].replace("[여성/남성 불안", "[남성 불안"),
        f"",
        _AVOIDANCE_TEXT[mav_t].replace("[여성/남성 회피", "[남성 회피"),
    ]
    return "\n".join(lines)

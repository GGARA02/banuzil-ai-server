# ============================================================
# services/dspy_modules/bullet_module.py
# 총알잡기 감지 DSPy 모듈
#
# 기존: build_bullet_detect_prompt() → call_llm() → json.loads()
# 현재: BulletDetect Signature → dspy.Predict → 타입된 출력
#
# 데이터 쌓이면:
#   detector = BulletDetector()
#   optimizer = dspy.BootstrapFewShot(metric=bullet_accuracy)
#   optimized = optimizer.compile(detector, trainset=examples)
#   optimized.save("bullet_optimized.json")
# ============================================================

import dspy
from services.dspy_modules import ensure_configured


class BulletDetect(dspy.Signature):
    """
    EFT 커플 상담 중 내담자 발화에서 방어적·공격적 발화(총알)를 감지한다.

    총알 유형:
    - Reactive: 즉각적 비난·공격·분노 폭발. 예) "너 때문이야", "항상 그런 식이잖아"
    - Mistrust: 냉소적 불신·거부·관계 포기. 예) "어차피 안 변해", "믿을 수가 없어"
    - None: 일반 발화 (총알 없음)

    EFT 단계별 민감도:
    - 1단계: 방어 패턴 탐색 중 → Reactive/Mistrust 모두 적극 감지
    - 2단계: 취약성 표현 단계 → 퇴행적 Mistrust에 특히 주의
    - 3단계: 통합 단계 → 재발하는 Reactive에 주의
    """
    reply: str = dspy.InputField(desc="내담자 발화 텍스트")
    eft_stage: int = dspy.InputField(desc="현재 EFT 단계 (1/2/3)")
    is_female: bool = dspy.InputField(desc="True=여성 내담자, False=남성 내담자")

    bullet_detected: bool = dspy.OutputField(desc="총알 감지 여부")
    bullet_type: str = dspy.OutputField(desc="Reactive / Mistrust / None 중 하나만 출력")
    confidence: float = dspy.OutputField(desc="감지 신뢰도 0.0~1.0")
    suggested_intervention: str = dspy.OutputField(
        desc="AI 상담사의 권장 개입 방식 한 문장 (한국어)"
    )


class BulletDetector(dspy.Module):
    def __init__(self):
        ensure_configured()
        self.predict = dspy.Predict(BulletDetect)

    def forward(self, reply: str, eft_stage: int, is_female: bool) -> dict:
        result = self.predict(
            reply=reply,
            eft_stage=eft_stage,
            is_female=is_female,
        )

        bullet_type = result.bullet_type
        if bullet_type not in ("Reactive", "Mistrust", "None"):
            bullet_type = "None"

        try:
            confidence = max(0.0, min(1.0, float(result.confidence)))
        except (ValueError, TypeError):
            confidence = 0.0

        return {
            "bullet_detected":      bool(result.bullet_detected),
            "bullet_type":          bullet_type,
            "confidence":           confidence,
            "suggested_intervention": result.suggested_intervention or "",
        }


# 싱글턴
_detector = None


def get_bullet_detector() -> BulletDetector:
    global _detector
    if _detector is None:
        _detector = BulletDetector()
    return _detector

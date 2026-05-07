# ============================================================
# services/dspy_modules/bullet_module.py
# 총알잡기 감지 DSPy 모듈
#
# 기본: build_bullet_detect_prompt() → call_llm() → json.loads()
# DSPy 최적화 파일 있으면: dspy.Predict(BulletDetect) → 타입된 출력
#
# 최적화 파일 위치: dspy_optimized/bullet/v1.json, v2.json, ...
# 자동으로 최신 버전 로드. 파일 없으면 기존 프롬프트 사용.
# ============================================================

import os
from pathlib import Path

import dspy
from services.dspy_modules import ensure_configured

_OPT_DIR = Path(os.path.dirname(__file__)).parent.parent / "dspy_optimized" / "bullet"


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
        self.is_optimized = False
        self.version = "default"
        self._load_latest()

    def _load_latest(self):
        """dspy_optimized/bullet/ 에서 최신 버전 자동 로드"""
        if not _OPT_DIR.exists():
            return
        versions = sorted(_OPT_DIR.glob("v*.json"))
        if versions:
            self.load(str(versions[-1]))
            self.is_optimized = True
            self.version = versions[-1].stem  # "v1", "v2", ...

    def forward(self, reply: str, eft_stage: int, is_female: bool) -> dict:
        if self.is_optimized:
            return self._dspy_forward(reply, eft_stage, is_female)
        return self._default_forward(reply, eft_stage, is_female)

    def _dspy_forward(self, reply, eft_stage, is_female):
        """DSPy 최적화 버전 — Predict 자동 프롬프트"""
        result = self.predict(
            reply=reply, eft_stage=eft_stage, is_female=is_female,
        )
        return self._validate(result)

    def _default_forward(self, reply, eft_stage, is_female):
        """기본값 — 기존 프롬프트 + DSPy LM 직접 호출"""
        from config.prompts.stage_prompts import build_bullet_detect_prompt
        import json

        prompt = build_bullet_detect_prompt(is_female, eft_stage, reply)
        lm = dspy.settings.lm
        raw_list = lm(prompt)
        raw = raw_list[0] if raw_list else ""

        try:
            clean = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
        except Exception:
            parsed = {
                "bullet_detected": False, "bullet_type": "None",
                "confidence": 0.0, "suggested_intervention": "",
            }

        class _Result:
            pass
        r = _Result()
        r.bullet_detected = parsed.get("bullet_detected", False)
        r.bullet_type = parsed.get("bullet_type", "None")
        r.confidence = parsed.get("confidence", 0.0)
        r.suggested_intervention = parsed.get("suggested_intervention", "")
        return self._validate(r)

    def _validate(self, result):
        """공통 검증 로직"""
        bullet_type = result.bullet_type
        if bullet_type not in ("Reactive", "Mistrust", "None"):
            bullet_type = "None"

        try:
            confidence = max(0.0, min(1.0, float(result.confidence)))
        except (ValueError, TypeError):
            confidence = 0.0

        return {
            "bullet_detected":       bool(result.bullet_detected),
            "bullet_type":           bullet_type,
            "confidence":            confidence,
            "suggested_intervention": result.suggested_intervention or "",
        }


# 싱글턴
_detector = None


def get_bullet_detector() -> BulletDetector:
    global _detector
    if _detector is None:
        _detector = BulletDetector()
    return _detector

# ============================================================
# services/dspy_modules/neutrality_module.py
# 중립성 검사 DSPy 모듈
#
# 기본: build_neutrality_prompt() → call_llm() → json.loads()
# DSPy 최적화 파일 있으면: dspy.Predict(NeutralityCheck) → 타입된 출력
#
# 최적화 파일 위치: dspy_optimized/neutrality/v1.json, v2.json, ...
# ============================================================

import os
from pathlib import Path

import dspy
from services.dspy_modules import ensure_configured

_OPT_DIR = Path(os.path.dirname(__file__)).parent.parent / "dspy_optimized" / "neutrality"


class NeutralityCheck(dspy.Signature):
    """
    EFT 커플 상담 AI 응답이 여성 또는 남성 중 한쪽에 편향됐는지 검사한다.

    편향 판단 기준:
    - 한쪽 발화에만 공감하거나 한쪽 입장을 지지하는 언어
    - 갈등 원인을 한쪽에 귀속시키는 표현
    - 한쪽에만 행동 변화를 요구하는 표현
    - 한쪽 응답이 현저히 더 길거나 따뜻한 경우

    점수 기준:
    5 = 완전 중립 / 4 = 대체로 중립 (경미한 불균형) /
    3 = 약간 편향 (경고 수준) / 2 = 명확한 편향 / 1 = 심각한 편향
    """
    f_reply: str = dspy.InputField(desc="여성 내담자 발화")
    m_reply: str = dspy.InputField(desc="남성 내담자 발화")
    f_response: str = dspy.InputField(desc="AI의 여성 내담자용 응답")
    m_response: str = dspy.InputField(desc="AI의 남성 내담자용 응답")
    classification: str = dspy.InputField(desc="커플 결합 유형 (32분류)")
    eft_stage: int = dspy.InputField(desc="현재 EFT 단계 (1/2/3)")

    neutrality_score: float = dspy.OutputField(desc="중립성 점수 1~5")
    bias_direction: str = dspy.OutputField(
        desc="toward_f(여성 편향) / toward_m(남성 편향) / none 중 하나만 출력"
    )
    violations: str = dspy.OutputField(
        desc="감지된 편향 항목들을 세미콜론(;)으로 구분. 없으면 빈 문자열"
    )
    reasoning: str = dspy.OutputField(desc="판단 근거 한 문장 (한국어)")
    feedback_for_regen: str = dspy.OutputField(
        desc="재생성 시 반드시 지켜야 할 교정 지침. 편향 없으면 빈 문자열"
    )


class NeutralityJudge(dspy.Module):
    def __init__(self):
        ensure_configured()
        self.predict = dspy.Predict(NeutralityCheck)
        self.is_optimized = False
        self.version = "default"
        self._load_latest()

    def _load_latest(self):
        if not _OPT_DIR.exists():
            return
        versions = sorted(_OPT_DIR.glob("v*.json"))
        if versions:
            self.load(str(versions[-1]))
            self.is_optimized = True
            self.version = versions[-1].stem

    def forward(
        self,
        f_reply: str, m_reply: str,
        f_response: str, m_response: str,
        classification: str,
        eft_stage: int,
        couple_profile=None,  # _default_forward에서 사용
    ) -> dict:
        if self.is_optimized:
            return self._dspy_forward(
                f_reply, m_reply, f_response, m_response,
                classification, eft_stage,
            )
        return self._default_forward(
            f_reply, m_reply, f_response, m_response,
            eft_stage, couple_profile,
        )

    def _dspy_forward(self, f_reply, m_reply, f_response, m_response,
                      classification, eft_stage):
        """DSPy 최적화 버전"""
        result = self.predict(
            f_reply=f_reply, m_reply=m_reply,
            f_response=f_response, m_response=m_response,
            classification=classification,
            eft_stage=eft_stage,
        )
        return self._to_dict(result)

    def _default_forward(self, f_reply, m_reply, f_response, m_response,
                         eft_stage, couple_profile):
        """기본값 — 기존 프롬프트 + DSPy LM 직접 호출"""
        from config.prompts.neutrality_check import build_neutrality_prompt
        import json

        prompt = build_neutrality_prompt(
            f_reply=f_reply, m_reply=m_reply,
            f_response=f_response, m_response=m_response,
            couple_profile=couple_profile,
            eft_stage=eft_stage,
        )

        system = "당신은 커플 상담 AI 응답의 중립성을 채점하는 검증자입니다. JSON만 출력하세요."
        lm = dspy.settings.lm
        raw_list = lm(f"{system}\n\n{prompt}")
        raw = raw_list[0] if raw_list else ""

        try:
            clean = raw.replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
        except Exception:
            parsed = {
                "neutrality_score": 4, "bias_direction": "none",
                "violations": [], "reasoning": "채점 파싱 실패 — 기본값 통과 처리",
                "feedback_for_regen": "",
            }

        class _Result:
            pass
        r = _Result()
        r.neutrality_score = parsed.get("neutrality_score", 4)
        r.bias_direction = parsed.get("bias_direction", "none")
        r.violations = ";".join(parsed.get("violations", []))
        r.reasoning = parsed.get("reasoning", "")
        r.feedback_for_regen = parsed.get("feedback_for_regen", "")
        return self._to_dict(r)

    def _to_dict(self, result):
        """공통 검증 + dict 변환"""
        try:
            score = max(1.0, min(5.0, float(result.neutrality_score)))
        except (ValueError, TypeError):
            score = 4.0

        direction = result.bias_direction
        if direction not in ("toward_f", "toward_m", "none"):
            direction = "none"

        violations_raw = result.violations or ""
        if isinstance(violations_raw, list):
            violations = violations_raw
        else:
            violations = [v.strip() for v in violations_raw.split(";") if v.strip()]

        return {
            "neutrality_score":   score,
            "bias_direction":     direction,
            "violations":         violations,
            "reasoning":          result.reasoning or "",
            "feedback_for_regen": result.feedback_for_regen or "",
        }


# 싱글턴
_judge = None


def get_neutrality_judge() -> NeutralityJudge:
    global _judge
    if _judge is None:
        _judge = NeutralityJudge()
    return _judge

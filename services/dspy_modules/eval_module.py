# ============================================================
# services/dspy_modules/eval_module.py
# Self-Refine 품질 평가 DSPy 모듈
#
# 기존: _REFINE_EVAL_SYSTEM + eval_prompt → call_llm() → json.loads()
# 현재: EFTEval Signature → dspy.Predict → 타입된 점수 출력
#
# 데이터 쌓이면:
#   optimizer = dspy.BootstrapFewShot(metric=lambda ex, pred: pred.weighted_avg >= 4.0)
#   optimized = optimizer.compile(EFTEvaluator(), trainset=examples)
# ============================================================

import dspy
from services.dspy_modules import ensure_configured


class EFTEval(dspy.Signature):
    """
    EFT 커플 상담 AI 응답의 품질을 6개 척도로 1~5점 채점한다.

    채점 기준:
    - neutrality (중립성): 여성·남성 양측에 공평한가? 한쪽 입장을 편드는 표현이 없는가?
    - validation_depth (타당화 깊이): 표면 감정(분노·냉담)이 아닌 1차 정서(두려움·외로움·무가치감)까지 도달했는가?
    - attach_coherence (애착 정합성): ECR-R 기반 애착 유형과 32분류 전략이 응답에 반영됐는가?
    - cycle_reframing (사이클 재구성): 갈등을 '부정적 상호작용 사이클'이라는 EFT 프레임으로 제시했는가?
    - actionability (행동 가능성): 다음 단계가 구체적이고 실행 가능한가?
    - safety (안전성): 위험 신호를 적절히 처리했는가? 폭력·학대를 정상화하지 않는가?
    """
    f_reply: str = dspy.InputField(desc="여성 내담자 발화")
    m_reply: str = dspy.InputField(desc="남성 내담자 발화")
    f_response: str = dspy.InputField(desc="AI의 여성 내담자용 응답")
    m_response: str = dspy.InputField(desc="AI의 남성 내담자용 응답")
    classification: str = dspy.InputField(desc="커플 결합 유형 레이블 (32분류)")
    f_attach_type: str = dspy.InputField(desc="여성 애착 유형 (안정/불안/거부회피/공포회피)")
    m_attach_type: str = dspy.InputField(desc="남성 애착 유형 (안정/불안/거부회피/공포회피)")
    eft_stage: int = dspy.InputField(desc="현재 EFT 단계 (1/2/3)")

    neutrality: float = dspy.OutputField(desc="중립성 점수 1~5")
    validation_depth: float = dspy.OutputField(desc="타당화 깊이 점수 1~5")
    attach_coherence: float = dspy.OutputField(desc="애착 정합성 점수 1~5")
    cycle_reframing: float = dspy.OutputField(desc="사이클 재구성 점수 1~5")
    actionability: float = dspy.OutputField(desc="행동 가능성 점수 1~5")
    safety: float = dspy.OutputField(desc="안전성 점수 1~5")
    improvement_hints: str = dspy.OutputField(
        desc="미달 척도의 개선 방향 한 문장 (한국어). 모두 통과면 빈 문자열"
    )


class EFTEvaluator(dspy.Module):
    def __init__(self):
        ensure_configured()
        self.predict = dspy.Predict(EFTEval)

    def forward(
        self,
        f_reply: str, m_reply: str,
        f_response: str, m_response: str,
        classification: str,
        f_attach_type: str, m_attach_type: str,
        eft_stage: int,
    ) -> dict:
        result = self.predict(
            f_reply=f_reply,
            m_reply=m_reply,
            f_response=f_response,
            m_response=m_response,
            classification=classification,
            f_attach_type=f_attach_type,
            m_attach_type=m_attach_type,
            eft_stage=eft_stage,
        )

        def _clamp(v):
            try:
                return max(1.0, min(5.0, float(v)))
            except (ValueError, TypeError):
                return 3.0

        return {
            "neutrality":        _clamp(result.neutrality),
            "validation_depth":  _clamp(result.validation_depth),
            "attach_coherence":  _clamp(result.attach_coherence),
            "cycle_reframing":   _clamp(result.cycle_reframing),
            "actionability":     _clamp(result.actionability),
            "safety":            _clamp(result.safety),
            "improvement_hints": result.improvement_hints or "",
        }


# 싱글턴
_evaluator = None


def get_eft_evaluator() -> EFTEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = EFTEvaluator()
    return _evaluator

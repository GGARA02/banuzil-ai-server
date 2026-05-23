# ============================================================
# services/dspy_modules/eval_module.py
# Self-Refine 품질 평가 DSPy 모듈
#
# 기본: 인라인 eval_prompt → call_llm() → json.loads()
# DSPy 최적화 파일 있으면: dspy.Predict(EFTEval) → 타입된 점수 출력
#
# 최적화 파일 위치: dspy_optimized/eval/v1.json, v2.json, ...
# ============================================================

import os
from pathlib import Path

import dspy
from services.dspy_modules import ensure_configured

_OPT_DIR = Path(os.path.dirname(__file__)).parent.parent / "dspy_optimized" / "eval"


class EFTEval(dspy.Signature):
    """
    EFT 커플 상담 AI 응답의 품질을 6개 척도로 1~5점 채점한다.

    채점 기준:
    - neutrality (중립성): 여성·남성 양측에 공평한가? 한쪽 입장을 편드는 표현이 없는가?
    - validation_depth (타당화 깊이): 표면 감정이 아닌 1차 정서까지 도달했는가?
    - attach_coherence (애착 정합성): ECR-R 기반 애착 유형과 32분류 전략이 반영됐는가?
    - cycle_reframing (사이클 재구성): 갈등을 부정적 상호작용 사이클 프레임으로 제시했는가?
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


_REFINE_EVAL_SYSTEM = """너는 EFT 커플 상담 응답 품질 평가 전문가다. 아래 기준에 따라 6개 척도를 채점하고 JSON만 출력하라."""


class EFTEvaluator(dspy.Module):
    def __init__(self):
        ensure_configured()
        self.predict = dspy.Predict(EFTEval)
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
        f_attach_type: str, m_attach_type: str,
        eft_stage: int,
    ) -> dict:
        if self.is_optimized:
            return self._dspy_forward(
                f_reply, m_reply, f_response, m_response,
                classification, f_attach_type, m_attach_type, eft_stage,
            )
        return self._default_forward(
            f_reply, m_reply, f_response, m_response,
            classification, f_attach_type, m_attach_type, eft_stage,
        )

    def _dspy_forward(self, f_reply, m_reply, f_response, m_response,
                      classification, f_attach_type, m_attach_type, eft_stage):
        """DSPy 최적화 버전"""
        result = self.predict(
            f_reply=f_reply, m_reply=m_reply,
            f_response=f_response, m_response=m_response,
            classification=classification,
            f_attach_type=f_attach_type, m_attach_type=m_attach_type,
            eft_stage=eft_stage,
        )
        return self._to_dict(result)

    def _default_forward(self, f_reply, m_reply, f_response, m_response,
                         classification, f_attach_type, m_attach_type, eft_stage):
        """기본값 — 기존 프롬프트 + DSPy LM 직접 호출"""
        import json

        eval_prompt = f"""[평가 대상]
여성 내담자 발화: {f_reply}
남성 내담자 발화: {m_reply}
AI → 여성 응답: {f_response}
AI → 남성 응답: {m_response}

[커플 정보]
결합 분류: {classification}
여성 유형: {f_attach_type} / 남성 유형: {m_attach_type}
EFT 단계: {eft_stage}단계

[6개 척도 채점 기준]
1. neutrality (구조적 중립성, 가중치 20%): 양측에 공평한가? 한쪽을 편드는가? (상대방의 1차 감정을 조심스럽게 추론하는 EFT 공감적 추론은 양측 대칭이면 편향 아님)
2. validation_depth (정서 타당화 깊이, 20%): 1차 정서까지 도달했는가?
3. attach_coherence (애착 패턴 정합성, 20%): 32분류 + ECR-R 개입 전략이 반영됐는가?
4. cycle_reframing (사이클 재구조화, 15%): 갈등을 사이클 프레임으로 제시했는가?
5. actionability (행동 가능성, 15%): 구체적이고 실행 가능한 다음 단계가 있는가?
6. safety (안전성, 10%): 위험 신호 처리가 적절한가? 폭력/학대 정상화가 없는가?

각 척도 1~5점으로 채점. JSON만 출력:
{{"neutrality": 점수, "validation_depth": 점수, "attach_coherence": 점수, "cycle_reframing": 점수, "actionability": 점수, "safety": 점수, "improvement_hints": "가중평균 미달 척도들에 대한 개선 방향 한 문장"}}"""

        lm = dspy.settings.lm
        raw_list = lm(f"{_REFINE_EVAL_SYSTEM}\n\n{eval_prompt}")
        raw = raw_list[0] if raw_list else ""

        try:
            clean = raw.replace("```json", "").replace("```", "").strip()
            scores = json.loads(clean)
        except Exception:
            scores = {}

        class _Result:
            pass
        r = _Result()
        r.neutrality = scores.get("neutrality", 3.0)
        r.validation_depth = scores.get("validation_depth", 3.0)
        r.attach_coherence = scores.get("attach_coherence", 3.0)
        r.cycle_reframing = scores.get("cycle_reframing", 3.0)
        r.actionability = scores.get("actionability", 3.0)
        r.safety = scores.get("safety", 3.0)
        r.improvement_hints = scores.get("improvement_hints", "")
        return self._to_dict(r)

    def _to_dict(self, result):
        """공통 검증 + dict 변환"""
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

# ============================================================
# services/dspy_modules/__init__.py
# DSPy LM 전역 설정
#
# EVAL_MODEL_NAME(기본: gpt-4o-mini)을 기본 LM으로 설정.
# 데이터 쌓이면 각 모듈에 dspy.Optimizer를 붙여 자동 튜닝 가능.
# ============================================================

import dspy
from config.settings import EVAL_MODEL_NAME, TEMPERATURE

_configured = False


def ensure_configured():
    """DSPy LM 최초 1회 초기화 (싱글턴)"""
    global _configured
    if not _configured:
        lm = dspy.LM(
            f"openai/{EVAL_MODEL_NAME}",
            temperature=TEMPERATURE,
            cache=False,        # 상담은 매번 다른 응답이 필요
        )
        dspy.configure(lm=lm)
        _configured = True

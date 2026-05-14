# ============================================================
# config/settings.py — 전체 설정 변수 중앙화
# 모든 AI 파라미터를 여기서 관리. 실제 배포 시 .env로 분리.
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM 설정 ──────────────────────────────────────────────
OPENAI_API_KEY:    str   = os.getenv("OPENAI_API_KEY", "")
MODEL_NAME:        str   = os.getenv("MODEL_NAME", "gpt-4o")          # 기본 모델 (gpt-4.5 출시 후 변경)
EVAL_MODEL_NAME:   str   = os.getenv("EVAL_MODEL_NAME", "gpt-4o-mini")# 평가용 경량 모델
TEMPERATURE:       float = float(os.getenv("TEMPERATURE", "0.7"))

# ── 토큰 / 글자수 제한 ──────────────────────────────────────
MAX_INPUT_CHARS:   int   = int(os.getenv("MAX_INPUT_CHARS",  "500"))   # 내담자 입력 최대 글자수
MAX_OUTPUT_TOKENS: int   = int(os.getenv("MAX_OUTPUT_TOKENS","800"))   # 상담 응답 최대 토큰
REPORT_MAX_TOKENS: int   = int(os.getenv("REPORT_MAX_TOKENS","3000"))  # 최종 보고서 최대 토큰
EVAL_MAX_TOKENS:   int   = int(os.getenv("EVAL_MAX_TOKENS",  "600"))   # EFT 진행 평가 최대 토큰
BULLET_MAX_TOKENS: int   = int(os.getenv("BULLET_MAX_TOKENS","300"))   # 총알잡기 감지 최대 토큰

# ── ECR-R 애착 가중치 구간 컷오프 ─────────────────────────
# 김성현(2004) 한국판 컷오프: 불안 2.61 / 회피 2.33
ANXIETY_CUTOFF:   float  = float(os.getenv("ANXIETY_CUTOFF",  "2.61"))
AVOIDANCE_CUTOFF: float  = float(os.getenv("AVOIDANCE_CUTOFF","2.33"))
# Mid tier 상한 = cutoff + TIER_RANGE_OFFSET
TIER_RANGE_OFFSET: float = float(os.getenv("TIER_RANGE_OFFSET","1.5"))

# ── MBTI 반영 비중 ─────────────────────────────────────────
MBTI_WEIGHT: float = float(os.getenv("MBTI_WEIGHT","0.2"))             # 0~1, 기본 낮게 유지

# ── 애착 분류 프롬프트 반영 강도 ──────────────────────────────
# 0.0: 32분류/개입지침 프롬프트에서 완전 제거
# 0.5: 요약본만 삽입
# 1.0: 전체 삽입 (기존 동작)
ATTACHMENT_PROMPT_WEIGHT: float = float(os.getenv("ATTACHMENT_PROMPT_WEIGHT","0.5"))

# ── KoELECTRA 감성 AI 반영 설정 ──────────────────────────────
EMOTION_WEIGHT: float = float(os.getenv("EMOTION_WEIGHT","0.3"))       # 감성 결과 프롬프트 반영 강도

# ── 총알잡기 감지 설정 ─────────────────────────────────────
BULLET_DETECTION_ENABLED: bool  = os.getenv("BULLET_DETECTION_ENABLED","true").lower() == "true"
# 총알 감지 민감도: 0~1. 공포회피형 결합 시 코드에서 낮춰서 적용
BULLET_THRESHOLD: float = float(os.getenv("BULLET_THRESHOLD","0.6"))

# ── Self-Refine 설정 ──────────────────────────────────────
MAX_REFINE: int = int(os.getenv("MAX_REFINE","3"))    # 최대 재생성 횟수
# 6개 척도 가중치 (합산 1.0)
EVAL_WEIGHTS = {
    "neutrality":        float(os.getenv("WEIGHT_NEUTRALITY",        "0.30")),
    "safety":            float(os.getenv("WEIGHT_SAFETY",            "0.20")),
    "validation_depth":  float(os.getenv("WEIGHT_VALIDATION_DEPTH",  "0.15")),
    "attach_coherence":  float(os.getenv("WEIGHT_ATTACH_COHERENCE",  "0.13")),
    "cycle_reframing":   float(os.getenv("WEIGHT_CYCLE_REFRAMING",   "0.12")),
    "actionability":     float(os.getenv("WEIGHT_ACTIONABILITY",     "0.10")),
}
EVAL_PASS_SCORE:   float = float(os.getenv("EVAL_PASS_SCORE",   "3.5"))  # 가중평균 통과 기준
SAFETY_GATE_SCORE: float = float(os.getenv("SAFETY_GATE_SCORE", "3.0"))  # 안전성 게이팅 기준

# ── 중립 검사 AI 설정 ─────────────────────────────────────
NEUTRALITY_MODEL:       str   = os.getenv("NEUTRALITY_MODEL",       "gpt-4o-mini")
NEUTRALITY_PASS_SCORE:  float = float(os.getenv("NEUTRALITY_PASS_SCORE",  "3.0"))
NEUTRALITY_WARN_SCORE:  float = float(os.getenv("NEUTRALITY_WARN_SCORE",  "4.0"))
NEUTRALITY_MAX_TOKENS:  int   = int(os.getenv("NEUTRALITY_MAX_TOKENS",    "400"))

# ── EFT 단계 전환 설정 ────────────────────────────────────
MIN_STAGE_ROUNDS: int = int(os.getenv("MIN_STAGE_ROUNDS","2"))   # 단계 전환 최소 누적 라운드

# ── 위험 감지 임계값 ──────────────────────────────────────
RISK_DETECTION_THRESHOLD: float = float(os.getenv("RISK_DETECTION_THRESHOLD","0.85"))
# 공포회피형 결합 시 임계값 (더 민감하게)
RISK_DETECTION_THRESHOLD_IPV: float = float(os.getenv("RISK_DETECTION_THRESHOLD_IPV","0.70"))

# ── Supabase ─────────────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

# ── Spring 서버 연동 ──────────────────────────────────────
SPRING_BASE_URL: str = os.getenv("SPRING_BASE_URL","http://localhost:8080")

# ── RAG (차후 구현) ───────────────────────────────────────
PGVECTOR_URL: str = os.getenv("PGVECTOR_URL","postgresql://localhost:5432/banuzil")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL","text-embedding-3-small")

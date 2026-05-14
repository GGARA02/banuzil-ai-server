# 바느질 AI 서버 — 코드 전반 설명서

## 목차
1. [프로젝트 개요](#1-프로젝트-개요)
2. [기술 스택](#2-기술-스택)
3. [파일 구조](#3-파일-구조)
4. [핵심 개념](#4-핵심-개념)
5. [LangGraph 실행 흐름](#5-langgraph-실행-흐름)
6. [모듈별 설명](#6-모듈별-설명)
7. [설정 변수 목록](#7-설정-변수-목록)
8. [세션 구조](#8-세션-구조)
9. [실행 방법](#9-실행-방법)

---

## 1. 프로젝트 개요

바느질 AI 서버는 두 가지 기능을 제공하는 FastAPI 서버다.

| 기능 | 설명 |
|------|------|
| **EFT 커플 상담 AI** | Emotionally Focused Therapy 기반. 커플의 애착 유형을 분석하고 3단계 상담을 진행. LangGraph 상태 그래프로 구현. |
| **감성 분석 API** | 발화 텍스트의 감정을 분류. KoELECTRA 파인튜닝 모델(대분류 2개 + 소분류 3개) 사용. |

**v3.0 Stateless 구조**: AI 서버는 상태를 보유하지 않는다. Spring이 세션 생명주기·히스토리·EFT 상태를 DB로 관리하고, 매 요청에 필요한 컨텍스트를 전달한다. AI 서버는 분석 결과 + 업데이트된 상태를 반환하는 순수 분석 엔진이다.

---

## 2. 기술 스택

| 분류 | 라이브러리 |
|------|-----------|
| 웹 프레임워크 | FastAPI + Uvicorn |
| AI 오케스트레이션 | LangGraph (상태 그래프) |
| LLM | OpenAI GPT (langchain-openai) |
| 감성 분석 모델 | KoELECTRA (HuggingFace Transformers + PyTorch) |
| 데이터 검증 | Pydantic v2 |
| 설정 관리 | python-dotenv |

---

## 3. 파일 구조

```
banuzil-ai-server/
│
├── main.py                          # FastAPI 진입점
│
├── routers/
│   ├── __init__.py
│   ├── counseling.py                # 상담 AI 엔드포인트 (3개: /ai/round-analyze, /ai/cycle, /ai/report)
│   └── emotion.py                   # 감성 분석 API 엔드포인트 (3개)
│
├── services/
│   ├── __init__.py
│   ├── llm.py                       # LLM 호출 공유 인터페이스
│   ├── emotion_service.py           # KoELECTRA 감성 분석 싱글턴 서비스
│   ├── session_store.py             # 인메모리 세션 저장소 (v3에서 미사용, 참고용)
│   ├── report_service.py            # 최종 보고서 생성
│   └── attachment_service.py        # ECR-R → 커플 프로파일 빌더 (32분류)
│
├── graphs/
│   ├── __init__.py
│   ├── eft_graph.py                 # LangGraph EFT 상태 그래프 (핵심)
│   └── neutrality_graph.py          # 중립성 검사 서브그래프
│
├── schemas/
│   ├── __init__.py
│   ├── request.py                   # Spring → Python 요청 스키마
│   └── response.py                  # Python → Spring 응답 스키마
│
├── config/
│   ├── __init__.py
│   ├── settings.py                  # 전체 설정 변수 (.env 기반)
│   ├── attachment_weight.py         # 애착 유형 분류 + 개입 강도 계산
│   └── prompts/
│       ├── __init__.py
│       ├── eft_base.py              # 시스템 프롬프트 / 유저 메시지 빌더
│       ├── stage_prompts.py         # EFT 단계별 지침 + 신호 평가 프롬프트
│       └── neutrality_check.py      # 중립성 검사 프롬프트 (DSPy 기본값)
│
├── data/
│   ├── raw/                         # 원본 학습 데이터 (xlsx)
│   ├── processed/                   # 전처리된 pkl 데이터
│   └── finetune/                    # KoELECTRA 파인튜닝 스크립트
│       ├── config.py                # 모델 설정 (레이블 수, 클래스 매핑 등)
│       ├── stage1_prep.py           # 데이터 전처리
│       ├── stage1_train.py          # 학습
│       ├── stage1_evaluate.py       # 평가
│       └── augment.py               # 데이터 증강
│
├── models/
│   ├── unweighted/                  # 감성 대분류 모델 (best_model.pt)
│   ├── low_weight/                  # 감성 소분류 모델 (best_model.pt)
│   └── weighted*/                   # 기타 실험 체크포인트
│
├── dspy_optimized/                  # DSPy 최적화 파일 (비어있으면 기존 프롬프트 사용)
│   ├── bullet/                      # v1.json, v2.json, ... 자동 최신 로드
│   ├── eval/
│   └── neutrality/
│
├── test_client.html                 # 브라우저 테스트 클라이언트 (http://localhost:8000/test)
├── .env                             # 환경 변수 (OPENAI_API_KEY만 교체)
└── docs/
    ├── OVERVIEW.md                  # 이 파일
    ├── API_SPEC.md                  # Spring 연동용 API 명세서
    ├── SETUP.md                     # 설치 및 실행 가이드
    └── CODE_ANALYSIS.md             # 핵심 코드 분석
```

---

## 4. 핵심 개념

### 4-1. EFT 3단계 구조

EFT(정서중심치료)는 커플 갈등을 3단계로 진행한다.

| 단계 | 목표 | 진입 조건 | 종료 조건 |
|------|------|-----------|-----------|
| **1단계** | 부정적 상호작용 사이클 탐색 | 세션 시작 | 양측 사이클 동의 (Spring DB 관리) |
| **2단계** | 1차 정서 접근 + 애착 욕구 표현 | 사이클 동의 완료 | 신호 기반 자동 전환 |
| **3단계** | 새로운 상호작용 패턴 통합 | 신호 누적 충족 | 양측 종료 동의 (Spring DB 관리) |

단계는 **후퇴하지 않는다** (1→2→3 단방향).

### 4-2. 애착 유형 분류 (ECR-R 기반)

ECR-R 설문으로 불안(anxiety)과 회피(avoidance) 점수(1.0~7.0)를 얻어 4가지 유형으로 분류한다.

| 유형 | 불안 | 회피 | 특징 |
|------|------|------|------|
| **안정형** | 낮음 | 낮음 | 신뢰, 안정적 관계 |
| **불안형(몰입형)** | 높음 | 낮음 | 버림받음 공포, 집착 |
| **거부회피형** | 낮음 | 높음 | 철회, 감정 억압 |
| **공포회피형(혼란형)** | 높음 | 높음 | 접근-회피 혼재, 외상 |

컷오프 기준: 불안 2.61 / 회피 2.33 (김성현 2004 한국판)

여성(f) × 남성(m) 조합으로 **32가지 결합 유형**이 정해지고, 각 유형마다 맞춤 상담 전략이 프롬프트에 삽입된다.

공포회피형이 포함된 결합은 `ipv_risk_flag = True` (데이트 폭력 위험군)로 표시된다.

### 4-3. SignalState (신호 누적)

`SignalState`는 EFT 진행 신호를 추적하는 객체다. 한 번 `True`가 된 신호는 다시 `False`로 돌아가지 않는다(단방향 누적).

```python
class SignalState:
    f: dict  # 여성 신호 {"emotional_awareness": bool, "vulnerability_expressed": bool, ...}
    m: dict  # 남성 신호
```

`stage_transition_check` 노드에서 LLM이 신호를 평가하고 `signals.merge()`로 누적한다.

### 4-4. 총알잡기 (Bullet Detection)

내담자의 발화 중 공격적·방어적 발화("총알")를 감지해 상담 개입 방식을 변경하는 기능.

| 유형 | 설명 |
|------|------|
| **Reactive** | 즉각적 감정 폭발, 비난 |
| **Mistrust** | 불신, 냉소적 거부 |
| **None** | 일반 발화 |

`BULLET_THRESHOLD` (기본 0.6) 이상의 신뢰도일 때만 총알로 판정. `BULLET_DETECTION_ENABLED = False`로 끌 수 있다.

### 4-5. Self-Refine 루프

AI 응답 생성 후 6개 척도로 채점하고, 기준 미달 시 응답을 재생성한다.

| 척도 | 가중치 | 설명 |
|------|--------|------|
| neutrality (구조적 중립성) | 30% | 양측에 공평한가 |
| safety (안전성) | 20% | 위험 신호 처리가 적절한가 |
| validation_depth (정서 타당화 깊이) | 15% | 1차 정서까지 도달했는가 |
| attach_coherence (애착 패턴 정합성) | 13% | 32분류 전략이 반영됐는가 |
| cycle_reframing (사이클 재구조화) | 12% | 갈등을 사이클 프레임으로 제시했는가 |
| actionability (행동 가능성) | 10% | 구체적 다음 단계가 있는가 |

가중 평균 < `EVAL_PASS_SCORE`(기본 3.5) 이거나 safety < `SAFETY_GATE_SCORE`(기본 3.0)이면 재생성. 최대 `MAX_REFINE`(기본 3)회.

`max_refine=0`으로 설정하면 Self-Refine 노드를 완전 스킵한다 (테스트 시 토큰 절약용).

### 4-6. 중립성 검사 (Neutrality Check)

response_generator 직후, self_refine 직전에 실행. AI 응답이 여성 또는 남성 중 한쪽에 편향됐는지 별도 LLM으로 검사한다.

- 점수 < 3.0 → 재생성 트리거 (`regen_triggered = True`)
- 3.0 ≤ 점수 < 4.0 → 다음 라운드에 경고 힌트 삽입
- 점수 ≥ 4.0 → 통과

---

## 5. LangGraph 실행 흐름

`POST /ai/round-analyze` 호출 시 아래 그래프가 실행된다.

```
[입력: f_reply, m_reply]
        │
        ▼
┌─────────────┐
│  risk_gate  │ ── 위험 키워드 감지 (LLM 없음, 순수 키워드 매칭)
└──────┬──────┘
       │ risk_flag=True → END (즉시 종료)
       │ risk_flag=False
       ▼
┌──────────────────┐
│ bullet_detector  │ ── LLM으로 Reactive/Mistrust 감지 (비활성화 가능)
└────────┬─────────┘
         ▼
┌─────────────────┐
│ emotion_inject  │ ── KoELECTRA 감성 분석 (f+m 병렬)
└───────┬─────────┘
        ▼
┌──────────────────┐
│ eft_stage_router │ ── stage_rounds 카운트 + 사이클 진입 선행 판단
└────────┬─────────┘
         ▼
┌────────────────────┐
│ response_generator │ ── GPT로 f응답+m응답 병렬 생성 (핵심)
└──────────┬─────────┘
           ▼
┌──────────────────┐
│ neutrality_check │ ── 편향 검사 → fail 시 response_generator로 루프백
└────────┬─────────┘
         │ pass
         ▼
┌─────────────┐
│ self_refine │ ── 6척도 채점 → 미달 시 response_generator로 루프백
└──────┬──────┘
       │ pass
       ▼
┌────────────────────────┐
│ stage_transition_check │ ── 단계 전환 신호 평가 + signals 누적
└────────────────────────┘
        │
       END
```

재생성 루프는 최대 `MAX_REFINE`(기본 3)회. 중립성 실패와 품질 실패 모두 같은 카운터를 공유한다.

---

## 6. 모듈별 설명

### `main.py`
FastAPI 앱 생성, CORS 설정, 라우터 등록. 테스트 클라이언트 HTML(`/test`) 서빙.

---

### `routers/counseling.py`
상담 AI 분석 엔드포인트 3개. Stateless — 세션 상태를 보유하지 않고 매 요청에서 받아 처리한다.

| 함수 | 엔드포인트 | 역할 |
|------|-----------|------|
| `round_analyze` | POST /ai/round-analyze | 전체 컨텍스트를 받아 LangGraph 실행 후 결과 + 업데이트된 상태 반환 |
| `cycle_analyze` | POST /ai/cycle | 사이클 탐색/정의 (동의는 Spring이 관리) |
| `generate_final_report` | POST /ai/report | 최종 보고서 생성 (히스토리 + 프로필을 직접 수신) |

내부 헬퍼:
- `_signals_to_dict(signals)` — SignalState → dict (응답 직렬화용)
- `_signals_from_dict(d)` — dict → SignalState (요청 역직렬화용)

---

### `routers/emotion.py`
감성 분석 엔드포인트 3개. `EmotionService` 싱글턴을 모듈 로딩 시 한 번 생성.

| 함수 | 엔드포인트 | 역할 |
|------|-----------|------|
| `analyze_emotion` | POST /emotion/analyze | 단일 발화 분석 |
| `analyze_emotion_batch` | POST /emotion/analyze/batch | 일괄 분석 |
| `health_check` | GET /emotion/health | 서버 상태 확인 |

---

### `services/llm.py`
LangChain + OpenAI 호출 공유 인터페이스. response_generator·stage_transition_check 노드에서 사용. DSPy 모듈(bullet/eval/neutrality)은 내부적으로 별도 LM을 사용한다.

| 함수 | 설명 |
|------|------|
| `call_llm(system_prompt, user_message, model, max_tokens)` | 단순 system+user 호출 |
| `call_llm_with_history(system_prompt, history, user_message, model, max_tokens)` | 대화 히스토리 포함 호출 |
| `_make_llm(model, max_tokens)` | ChatOpenAI 인스턴스 생성 (내부용) |

`history` 형식: `[{"role": "user"|"assistant", "content": str}]`

---

### `services/emotion_service.py`
KoELECTRA 기반 감성 분류. **싱글턴 패턴** — 서버 시작 시 한 번만 모델을 로드한다.

| 모델 | 디렉토리 | 역할 |
|------|----------|------|
| `cat_model` | `models/unweighted/` | 감성 대분류 Top-2 출력 |
| `det_model` | `models/low_weight/` | 감성 소분류 Top-3 출력 |

입력 형식: `[성별] {gender} [상황] {situation} [발화] {text}`

`analyze()` 반환값:
```json
{
  "input": {"gender": "여성", "situation": "연애", "text": "..."},
  "models": {"category_model": "unweighted", "detail_model": "low_weight"},
  "category": [{"rank": 1, "label": "분노", "score": 0.71}, ...],
  "detail":   [{"rank": 1, "label": "노여워하는", "score": 0.42}, ...]
}
```

---

### `services/session_store.py` (v3에서 미사용)
v2에서 사용하던 인메모리 세션 저장소. v3 Stateless 전환으로 라우터에서 참조하지 않는다. 참고용으로 남겨둠.

---

### `services/report_service.py`
상담 종료 후 여성/남성 각각의 보고서를 GPT로 병렬 생성.

`generate_report(couple_profile, f_history, m_history, cycle_definition)` → `(f_report, m_report)`

---

### `services/attachment_service.py`
ECR-R 점수를 받아 `CoupleProfile` 객체를 조립한다.

주요 함수:
- `build_couple_profile(session_id, f_anxiety, f_avoidance, f_mbti, f_situation, m_anxiety, m_avoidance, m_mbti, m_situation)` → `CoupleProfile`
- `get_couple_classification(f_type, m_type)` → 32분류 매핑 dict

`_COMBO_MAP`에 16가지 기본 결합(f×m 순서 포함 32가지)의 라벨, 위험도, 맥락 설명, 여성/남성별 상담 전략이 하드코딩되어 있다.

---

### `config/attachment_weight.py`
ECR-R 점수 → 애착 유형 분류 + 가중치 계산.

| 함수/클래스 | 설명 |
|------------|------|
| `classify_attachment_type(anxiety, avoidance)` | 4가지 유형 중 하나 반환 |
| `build_attachment_profile(anxiety, avoidance)` | `AttachmentProfile` 객체 생성 |
| `build_intervention_guide(f_profile, m_profile)` | 개입 강도 지침 텍스트 생성 |
| `AttachmentProfile` | anxiety_score, avoidance_score, attach_type, anxiety_weight, avoidance_weight, intervention_tier(LOW/MID/HIGH) |
| `CoupleProfile` | 커플 전체 프로파일. f_profile, m_profile, classification, ipv_risk_flag 등 포함 |

점수 → 가중치 변환:
- LOW tier (score < cutoff): 0.00 ~ 0.35
- MID tier (cutoff ≤ score < cutoff + 1.5): 0.35 ~ 0.70
- HIGH tier (그 이상): 0.70 ~ 1.00

---

### `graphs/eft_graph.py`
LangGraph 기반 EFT 상담 핵심 로직. 노드 7개 + 엣지 로직.

**주요 노드:**

| 노드 | 유형 | 설명 |
|------|------|------|
| `node_risk_gate` | 동기 | 위험 키워드 하드코딩 매칭. LLM 없음 |
| `node_bullet_detector` | 비동기 | LLM으로 총알 유형 판정 |
| `node_emotion_inject` | 비동기 | KoELECTRA 호출 (스레드풀에서 실행) |
| `node_eft_stage_router` | 동기 | stage_rounds 카운트 증가 + 사이클 진입 조건 선행 판단 (`is_cycle_round`) |
| `node_response_generator` | 비동기 | f+m 응답 GPT 병렬 생성 |
| `node_neutrality_check` | 비동기 | 중립성 검사 LLM 호출 |
| `node_self_refine` | 비동기 | 6척도 채점 + 재생성 결정 |
| `node_stage_transition_check` | 비동기 | 신호 평가 + 단계 전환 결정 |

**위험 키워드** (`_RISK_KEYWORDS_HARD`):
- 자해: ["자해", "손목", "긋"]
- 자살: ["자살", "죽고 싶", "뛰어내리", "목매", "극단적 선택"]
- 폭행: ["때리", "죽이", "폭행", "칼로", "협박", "죽여"]
- 스토킹: ["스토킹", "신상", "감금", "따라다"]
- 데이트폭력: ["강제로", "강간", "성폭"]
- IPV 위험군 추가: ["무서워", "두려워", "겁나", "맞아", "맞았", "밀쳐"]

**싱글턴 그래프:** `get_eft_graph()`로 호출. 최초 1회만 `build_eft_graph()`를 실행하고 이후에는 캐시된 인스턴스 반환.

---

### `graphs/neutrality_graph.py`
중립성 검사 전용 모듈. LangGraph 서브그래프 대신 노드 함수를 순차 호출로 실행.

| 함수 | 설명 |
|------|------|
| `node_llm_judge(state)` | LLM으로 편향 점수 JSON 출력 |
| `node_verdict(state)` | 점수 기반 pass/fail + 피드백 패키징 (LLM 없음) |
| `run_neutrality_check(...)` | 외부 호출용 진입점. 두 노드를 순서대로 실행 |

---

### `config/settings.py`
모든 AI 파라미터의 단일 진실 공급원. 자세한 목록은 [섹션 7](#7-설정-변수-목록) 참고.

---

### `data/finetune/`
KoELECTRA 파인튜닝 스크립트. **서버 실행과 무관** — 모델 학습/평가 전용.

| 파일 | 역할 |
|------|------|
| `config.py` | 레이블 수, 클래스 매핑, 모델명 등 학습 설정 |
| `stage1_prep.py` | 원본 xlsx → pkl 전처리 |
| `stage1_train.py` | 학습 실행 |
| `stage1_evaluate.py` | 검증 데이터 평가 |
| `augment.py` | 데이터 증강 |

---

## 7. 설정 변수 목록

`config/settings.py` — 모두 `.env`로 오버라이드 가능.

### LLM 설정
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `OPENAI_API_KEY` | `""` | OpenAI API 키 |
| `MODEL_NAME` | `"gpt-4o"` | 상담 응답 생성 모델 |
| `EVAL_MODEL_NAME` | `"gpt-4o-mini"` | 평가/채점용 경량 모델 |
| `TEMPERATURE` | `0.7` | 생성 다양성 |

### 토큰 제한
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MAX_INPUT_CHARS` | `500` | 내담자 입력 최대 글자수 |
| `MAX_OUTPUT_TOKENS` | `800` | 상담 응답 최대 토큰 |
| `REPORT_MAX_TOKENS` | `3000` | 최종 보고서 최대 토큰 |
| `EVAL_MAX_TOKENS` | `600` | EFT 진행 평가 최대 토큰 |

### 애착 분류 컷오프
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANXIETY_CUTOFF` | `2.61` | 불안 차원 컷오프 (김성현 2004) |
| `AVOIDANCE_CUTOFF` | `2.33` | 회피 차원 컷오프 |
| `TIER_RANGE_OFFSET` | `1.5` | MID tier 상한 오프셋 |
| `MBTI_WEIGHT` | `0.2` | MBTI 프롬프트 반영 비중 |
| `ATTACHMENT_PROMPT_WEIGHT` | `1.0` | 애착유형 상세 정보의 프롬프트 반영 강도 (0.0~1.0) |
| `EMOTION_WEIGHT` | `0.3` | KoELECTRA 결과 반영 강도 |

### 총알잡기
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BULLET_DETECTION_ENABLED` | `true` | 총알잡기 ON/OFF |
| `BULLET_THRESHOLD` | `0.6` | 총알 판정 최소 신뢰도 |

### Self-Refine
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MAX_REFINE` | `3` | 최대 재생성 횟수 |
| `EVAL_PASS_SCORE` | `3.5` | 가중평균 통과 기준 (1~5점) |
| `SAFETY_GATE_SCORE` | `3.0` | safety 척도 최소값 |
| `WEIGHT_NEUTRALITY` | `0.30` | 중립성 가중치 |
| `WEIGHT_SAFETY` | `0.20` | 안전성 가중치 |
| `WEIGHT_VALIDATION_DEPTH` | `0.15` | 타당화 깊이 가중치 |
| `WEIGHT_ATTACH_COHERENCE` | `0.13` | 애착 정합성 가중치 |
| `WEIGHT_CYCLE_REFRAMING` | `0.12` | 사이클 재구조화 가중치 |
| `WEIGHT_ACTIONABILITY` | `0.10` | 행동 가능성 가중치 |

### 중립성 검사
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `NEUTRALITY_MODEL` | `"gpt-4o-mini"` | 중립성 검사 모델 |
| `NEUTRALITY_PASS_SCORE` | `3.0` | 재생성 트리거 기준 |
| `NEUTRALITY_WARN_SCORE` | `4.0` | 경고 힌트 삽입 기준 |
| `NEUTRALITY_MAX_TOKENS` | `400` | 중립성 채점 최대 토큰 |

### EFT 단계 전환
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MIN_STAGE_ROUNDS` | `2` | 단계 전환 최소 누적 라운드 수 |
| `RISK_DETECTION_THRESHOLD` | `0.85` | 위험 감지 임계값 |
| `RISK_DETECTION_THRESHOLD_IPV` | `0.70` | IPV 위험군 임계값 (더 민감) |

---

## 8. Spring DB 저장 구조

v3에서 세션 상태는 Spring DB에 저장된다. AI 서버는 매 요청에서 아래 필드를 수신하고, 응답의 `updated_*` 필드로 갱신된 값을 반환한다.

| 필드 | 타입 | Spring DB 저장 | 설명 |
|------|------|---------------|------|
| `f_history` | `list[dict]` | JSONB (user1_message) | 여성 대화 히스토리 `[{"role": "user"\|"assistant", "content": str}]` |
| `m_history` | `list[dict]` | JSONB (user2_message) | 남성 대화 히스토리 |
| `eft_stage` | `int` | INTEGER | 현재 EFT 단계 (1/2/3) |
| `stage_rounds` | `dict[int,int]` | JSONB | 단계별 누적 라운드 수 `{1: n, 2: n, 3: n}` |
| `stage_progress` | `int` | INTEGER | 현재 단계 진행도 (0~100) |
| `signals` | `dict` | JSONB | EFT 진행 신호 `{"f": {...}, "m": {...}}` |
| `cycle_definition` | `str` | TEXT | 부정적 상호작용 사이클 정의 텍스트 |
| `cycle_agreed` | — | BOOLEAN x2 | Spring이 직접 관리 |
| `end_agreed` | — | BOOLEAN x2 | Spring이 직접 관리 |
| `round_num` | `int` | INTEGER | 누적 라운드 번호 |

---

## 9. 실행 방법

### 환경 설정

```bash
# .env 파일 생성
OPENAI_API_KEY=sk-...
MODEL_NAME=gpt-4o
EVAL_MODEL_NAME=gpt-4o-mini
```

### 서버 실행

```bash
# 개발 (자동 재로드)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 또는
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 엔드포인트 확인

- Swagger UI: `http://localhost:8000/docs`
- 헬스체크: `GET http://localhost:8000/emotion/health`

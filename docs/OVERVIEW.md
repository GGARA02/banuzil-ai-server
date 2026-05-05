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

Spring 백엔드 서버가 session_id와 커플의 발화를 보내면, 이 서버가 모든 상담 상태를 관리하고 AI 응답을 반환한다.

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
│   ├── counseling.py                # 상담 API 엔드포인트 (5개)
│   └── emotion.py                   # 감성 분석 API 엔드포인트 (3개)
│
├── services/
│   ├── __init__.py
│   ├── llm.py                       # LLM 호출 공유 인터페이스
│   ├── emotion_service.py           # KoELECTRA 감성 분석 싱글턴 서비스
│   ├── session_store.py             # 인메모리 세션 저장소
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
│       └── neutrality_check.py      # 중립성 검사 프롬프트 빌더
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
├── app/                             # 레거시 (v1 구조 잔재, 사용 안 함)
│   ├── main.py                      # v1 진입점 — 현재는 루트 main.py 사용
│   ├── routers/emotion.py           # → routers/emotion.py로 이전됨
│   └── services/emotion_service.py  # → services/emotion_service.py로 이전됨
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
| **1단계** | 부정적 상호작용 사이클 탐색 | 세션 시작 | 양측 사이클 동의 (`/cycle` 엔드포인트) |
| **2단계** | 1차 정서 접근 + 애착 욕구 표현 | 사이클 동의 완료 | 신호 기반 자동 전환 |
| **3단계** | 새로운 상호작용 패턴 통합 | 신호 누적 충족 | 양측 종료 동의 (`/end` 엔드포인트) |

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
| neutrality (구조적 중립성) | 20% | 양측에 공평한가 |
| validation_depth (정서 타당화 깊이) | 20% | 1차 정서까지 도달했는가 |
| attach_coherence (애착 패턴 정합성) | 20% | 32분류 전략이 반영됐는가 |
| cycle_reframing (사이클 재구조화) | 15% | 갈등을 사이클 프레임으로 제시했는가 |
| actionability (행동 가능성) | 15% | 구체적 다음 단계가 있는가 |
| safety (안전성) | 10% | 위험 신호 처리가 적절한가 |

가중 평균 < `EVAL_PASS_SCORE`(기본 4.0) 이거나 safety < `SAFETY_GATE_SCORE`(기본 3.0)이면 재생성. 최대 `MAX_REFINE`(기본 3)회.

### 4-6. 중립성 검사 (Neutrality Check)

response_generator 직후, self_refine 직전에 실행. AI 응답이 여성 또는 남성 중 한쪽에 편향됐는지 별도 LLM으로 검사한다.

- 점수 < 3.0 → 재생성 트리거 (`regen_triggered = True`)
- 3.0 ≤ 점수 < 4.0 → 다음 라운드에 경고 힌트 삽입
- 점수 ≥ 4.0 → 통과

---

## 5. LangGraph 실행 흐름

`POST /counseling/round` 호출 시 아래 그래프가 실행된다.

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
│ eft_stage_router │ ── stage_rounds 카운트 증가
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
상담 관련 엔드포인트 5개. 세션 상태를 `session_store`에서 꺼내고 → LangGraph 실행 → 다시 저장하는 패턴.

| 함수 | 엔드포인트 | 역할 |
|------|-----------|------|
| `create_session` | POST /counseling/session | 커플 프로파일 생성 + 세션 초기화 |
| `counseling_round` | POST /counseling/round | LangGraph 1회 실행 |
| `counseling_cycle` | POST /counseling/cycle | 사이클 탐색/정의/동의 처리 |
| `counseling_end` | POST /counseling/end | 3단계 종료 동의 수집 |
| `counseling_report` | POST /counseling/report | 최종 보고서 생성 |
| `delete_session` | DELETE /counseling/session/{id} | 세션 삭제 |

내부 헬퍼:
- `_signals_to_dict(signals)` — SignalState → dict (세션 저장용)
- `_signals_from_dict(d)` — dict → SignalState (세션 복원용)

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
LangChain + OpenAI 호출 공유 인터페이스. Phase 2에서 DSPy로 교체 시 이 파일만 수정한다.

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

### `services/session_store.py`
Python 딕셔너리 기반 인메모리 세션 저장소. 서버 재시작 시 초기화된다.

| 함수 | 설명 |
|------|------|
| `session_get(session_id)` | 세션 조회. 만료 시 None 반환 |
| `session_set(session_id, data)` | 세션 저장/갱신. TTL 리셋 |
| `session_delete(session_id)` | 세션 삭제 |
| `session_exists(session_id)` | 세션 존재 여부 |

`SESSION_TTL_HOURS = 24` (기본 24시간). Redis 교체 시 이 파일만 수정하면 된다.

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
| `node_eft_stage_router` | 동기 | stage_rounds 카운트 증가 |
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
| `BULLET_MAX_TOKENS` | `300` | 총알잡기 감지 최대 토큰 |

### 애착 분류 컷오프
| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANXIETY_CUTOFF` | `2.61` | 불안 차원 컷오프 (김성현 2004) |
| `AVOIDANCE_CUTOFF` | `2.33` | 회피 차원 컷오프 |
| `TIER_RANGE_OFFSET` | `1.5` | MID tier 상한 오프셋 |
| `MBTI_WEIGHT` | `0.2` | MBTI 프롬프트 반영 비중 |
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
| `EVAL_PASS_SCORE` | `4.0` | 가중평균 통과 기준 (1~5점) |
| `SAFETY_GATE_SCORE` | `3.0` | safety 척도 최소값 |
| `WEIGHT_NEUTRALITY` | `0.20` | 중립성 가중치 |
| `WEIGHT_VALIDATION_DEPTH` | `0.20` | 타당화 깊이 가중치 |
| `WEIGHT_ATTACH_COHERENCE` | `0.20` | 애착 정합성 가중치 |
| `WEIGHT_CYCLE_REFRAMING` | `0.15` | 사이클 재구조화 가중치 |
| `WEIGHT_ACTIONABILITY` | `0.15` | 행동 가능성 가중치 |
| `WEIGHT_SAFETY` | `0.10` | 안전성 가중치 |

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

## 8. 세션 구조

`session_store`에 저장되는 세션 dict 필드 목록.

| 필드 | 타입 | 설명 |
|------|------|------|
| `couple_profile` | `CoupleProfile` | 커플 애착 프로파일 전체 |
| `f_history` | `list[dict]` | 여성 대화 히스토리 `[{"role": "user"\|"assistant", "content": str}]` |
| `m_history` | `list[dict]` | 남성 대화 히스토리 |
| `eft_stage` | `int` | 현재 EFT 단계 (1/2/3) |
| `eft_step` | `int` | EFT 세부 스텝 (1~9, 현재 미세분화) |
| `stage_rounds` | `dict[int,int]` | 단계별 누적 라운드 수 `{1: n, 2: n, 3: n}` |
| `stage_progress` | `int` | 현재 단계 진행도 (0~100) |
| `signals` | `SignalState` | EFT 진행 신호 누적 객체 |
| `cycle_definition` | `str` | 부정적 상호작용 사이클 정의 텍스트 |
| `cycle_agreed` | `dict` | 사이클 동의 여부 `{"f": bool, "m": bool}` |
| `end_agreed` | `dict` | 종료 동의 여부 `{"f": bool, "m": bool}` |
| `round_num` | `int` | 누적 라운드 번호 |
| `model_name` | `str\|None` | 이 세션에서 사용할 모델명 오버라이드 |
| `bullet_enabled` | `bool\|None` | 총알잡기 ON/OFF 오버라이드 |
| `emotion_weight` | `float\|None` | 감성 반영 강도 오버라이드 |
| `needs_cycle_definition` | `bool` | 사이클 정의 필요 플래그 |
| `_expires_at` | `datetime` | 세션 만료 시각 (자동 관리) |

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
- 테스트 클라이언트: `http://localhost:8000/test`
- 헬스체크: `GET http://localhost:8000/emotion/health`

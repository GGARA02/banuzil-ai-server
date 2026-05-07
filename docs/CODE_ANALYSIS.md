# 바느질 AI 서버 — 코드 분석

모든 .py 파일의 역할, 동작 원리, 파일 간 연결 관계를 설명한다.
파이썬 초심자 기준으로 작성되었으며, 7단계(Stage)로 나뉜 학습 순서를 따른다.

---

## Stage 1 — 서버 뼈대

### 1. `main.py` — FastAPI 서버 진입점

- `FastAPI()` 인스턴스 생성 후 `include_router()`로 라우터 등록
- `/emotion` (감성 분석), `/counseling` (상담 AI) 두 라우터
- 서버 시작: `uvicorn main:app --reload`

### 2. `config/settings.py` — 전체 설정 변수 중앙화

모든 AI 파라미터를 여기서 관리. `.env` 파일에서 읽되, 없으면 기본값 사용.

| 카테고리 | 주요 변수 | 설명 |
|---|---|---|
| LLM | `MODEL_NAME("gpt-4o")`, `EVAL_MODEL_NAME("gpt-4o-mini")` | 상담용 풀모델 / 평가용 경량모델 |
| 토큰 제한 | `MAX_OUTPUT_TOKENS(800)`, `BULLET_MAX_TOKENS(300)` 등 | 용도별 최대 토큰 |
| ECR-R | `ANXIETY_CUTOFF(2.61)`, `AVOIDANCE_CUTOFF(2.33)` | 김성현(2004) 한국판 컷오프 |
| 총알잡기 | `BULLET_THRESHOLD(0.6)` | 감지 민감도 (0~1) |
| Self-Refine | `EVAL_WEIGHTS`, `EVAL_PASS_SCORE(4.0)`, `SAFETY_GATE_SCORE(3.0)` | 6개 척도 가중치 + 통과/안전 기준 |
| 중립 검사 | `NEUTRALITY_PASS_SCORE(3.0)`, `NEUTRALITY_WARN_SCORE(4.0)` | 중립성 통과/경고 기준 |
| 위험 감지 | `RISK_DETECTION_THRESHOLD(0.85)`, `RISK_DETECTION_THRESHOLD_IPV(0.70)` | 키워드 기반 위험 감지 (총알잡기와 별개) |

**EVAL_WEIGHTS 가중치 (합산 1.0):**

| 척도 | 가중치 | 설명 |
|---|---|---|
| neutrality | 0.30 | 양측 공평성 (최우선) |
| safety | 0.20 | 안전성 (하드게이트 별도) |
| validation_depth | 0.15 | 감정 타당화 깊이 |
| attach_coherence | 0.13 | 애착 유형 정합성 |
| cycle_reframing | 0.12 | 사이클 재구성 |
| actionability | 0.10 | 행동 가능성 |

중립성 + 안전성 = 50%. EFT 관련 척도는 가중치 대신 프롬프트 질로 보완하는 전략.

**위험감지 vs 총알잡기:**
- `RISK_DETECTION_THRESHOLD` — 자해/자살/폭력 키워드 매칭 → 즉시 상담 중단
- `BULLET_THRESHOLD` — GPT 기반 방어적/공격적 발화 감지 → 상담 전략 조정

### 3. `schemas/request.py` + `schemas/response.py` — Spring ↔ Python 데이터 계약

**Pydantic BaseModel** 기반. JSON 수신 시 타입 검증 + 범위 검사 자동 수행.

주요 스키마:

| 요청 | 응답 | 설명 |
|---|---|---|
| `SessionCreateRequest` | `SessionCreateResponse` | 세션 생성 (ECR-R 점수) |
| `CounselingRoundRequest` | `CounselingRoundResponse` | 매 라운드 (양측 발화 → 양측 응답) |
| `CycleConsentRequest` | `CycleExploreResponse` / `CycleDefinitionResponse` / `CycleAgreedResponse` | 사이클 탐색/정의/동의 |
| `ReportRequest` | `CounselingReportResponse` | 최종 보고서 (여성/남성 분리) |

`CounselingRoundResponse`의 핵심 필드:
- `f_message`, `m_message` — Spring이 내담자에게 전달할 상담 메시지
- `risk_flag` — True면 즉시 상담 중단
- `needs_cycle_definition` — True면 사이클 정의 화면 표시
- `eval_scores`, `neutrality_result`, `f_emotion`, `m_emotion` 등 — 디버깅/로깅용

`SessionCreateRequest`의 오버라이드 옵션 (`model_name`, `bullet_enabled`, `emotion_weight`):
- 기본은 settings.py 값 사용, Spring이 특정 세션에만 다른 설정을 적용할 때 사용

---

## Stage 2 — 감성 분석

### 4. `routers/emotion.py` — 감정 분석 API 라우터

| 엔드포인트 | 메서드 | 설명 |
|---|---|---|
| `/emotion/analyze` | POST | 단일 발화 감정 분석 |
| `/emotion/analyze/batch` | POST | 여러 발화 일괄 분석 |
| `/emotion/health` | GET | 서버 상태 + 모델 버전 확인 |

상담 내부에서도 쓰지만 디버깅용 독립 호출도 가능. `EmotionService`를 싱글턴으로 공유.

### 5. `services/emotion_service.py` — KoELECTRA 감성 분석 서비스

**MultiTaskEmotionModel 구조:**

```
텍스트 → Tokenizer → KcELECTRA 인코더(768차원) → Dropout
                                                    ├── category_head → 6개 대분류 점수
                                                    └── detail_head   → 63개 소분류 점수
```

- 기반 모델: `beomi/KcELECTRA-base-v2022` (HuggingFace에서 다운로드)
- 대분류: `models/unweighted/best_model.pt` → Top2 반환
- 소분류: `models/low_weight/best_model.pt` → Top3 반환
- 입력 포맷: `"[성별] 여성 [상황] 연애 [발화] 왜 연락을 안 해?"` (학습 시와 동일)

**싱글턴 패턴 (`__new__`)**: 모델 로딩이 무거우므로 (약 800MB) 최초 1회만 로딩, 이후 동일 인스턴스 재사용.

**GPU/CPU**: `torch.device("cuda" if torch.cuda.is_available() else "cpu")`. KcELECTRA는 경량 모델이라 CPU에서 50~200ms로 추론 가능. GPU 없어도 상담 응답 지연(2~5초)에 묻히는 수준.

`_load_finetune_config()`: `data/finetune/config.py`를 `importlib.util`로 직접 로드. 프로젝트의 `config/` 패키지와 이름 충돌을 피하기 위한 우회.

### 6. `config/attachment_weight.py` — 애착 유형 분류 + 개입 강도 지침

**ECR-R 4유형 분류:**

```
           회피 낮음         회피 높음
불안 높음 │ 불안형(몰입형)    │ 공포회피형(혼란형) │
불안 낮음 │ 안정형           │ 거부회피형        │
```

컷오프: 불안 2.61 / 회피 2.33 (김성현 2004 한국판 ECR-R)

**`_score_to_weight()`** — ECR-R 원점수(1~7)를 0~1 가중치로 3단계 변환:
- LOW (0.00~0.35): cutoff 미만
- MID (0.35~0.70): cutoff ~ cutoff+1.5
- HIGH (0.70~1.00): cutoff+1.5 초과

**`build_intervention_guide()`** — LOW/MID/HIGH별 상담 지침 텍스트 생성:
- LOW: "일반 EFT 개입으로 충분"
- MID: "명시적으로 다뤄라", "속도를 늦추고"
- HIGH: "압박 금지", "절대 압박하지 마라"
- 4차원(여불안, 여회피, 남불안, 남회피) 중 가장 극단적인 차원을 찾아 최우선 지침 부여

**IPV_RISK_COMBOS**: 공포회피형이 한 명이라도 포함되면 IPV(친밀한 파트너 폭력) 위험 플래그.

**데이터 클래스:**
- `AttachmentProfile` — 한 사람의 애착 프로파일 (점수, 유형, 가중치, 개입 강도)
- `CoupleProfile` — 커플 전체 (양측 프로파일 + 32분류 + 개입 지침 + IPV 플래그)

---

## Stage 3 — 상담 설정

### 7. `services/attachment_service.py` — 32분류 매핑 + CoupleProfile 조립

4유형 × 4유형 = **16조합, 양성별 32분류** 매핑 테이블 (`_COMBO_MAP`).

각 조합마다:
- `label` — 결합 이름 (예: "불안-거부회피 결합 (전형적 추구-철회 패턴)")
- `risk` — 위험도 (낮음/중간/높음/매우높음/최고위험)
- `context` — 공통 맥락 설명
- `f_desc` — 여성 전용 EFT 상담 지침 (여성 프롬프트에 삽입)
- `m_desc` — 남성 전용 EFT 상담 지침 (남성 프롬프트에 삽입)

**위험도 분포**: 안정-안정(1개 낮음) ~ 공포회피-공포회피(1개 최고위험, "AI 상담 한계 고지"). 공포회피형 포함 시 6개가 "매우높음".

**`build_couple_profile()`** — 최종 조립 함수:
1. `build_attachment_profile()` → 각자의 AttachmentProfile
2. `get_couple_classification()` → 32분류 매칭
3. `IPV_RISK_COMBOS` 체크 → IPV 위험 플래그
4. `build_intervention_guide()` → 개입 강도 지침

→ `CoupleProfile` 완성. 이후 상담 전체에서 참조됨.

### 8. `services/session_store.py` — 메모리 기반 세션 저장소

파이썬 딕셔너리 하나(`_store`)가 전체 세션 저장소.

| 함수 | 설명 |
|---|---|
| `session_get(id)` | 세션 조회 + 만료 체크 (lazy expiration) |
| `session_set(id, data)` | 저장/갱신 + 만료 시간 리셋 (24시간) |
| `session_delete(id)` | 삭제 |
| `session_exists(id)` | 존재 여부 (get 내부 호출로 만료 체크 포함) |

**한계**: 서버 재시작 시 세션 소멸, 다중 서버 시 세션 공유 불가.
**차후**: Redis로 교체 시 이 파일만 수정 (함수 인터페이스 동일 유지).

### 9. `services/llm.py` — GPT API 호출 유틸

LangChain `ChatOpenAI` 기반. 프로젝트의 모든 GPT 호출이 이 파일을 경유.

| 함수 | 용도 |
|---|---|
| `call_llm(system, user)` | 단발 호출 (보고서, 사이클 정의 등) |
| `call_llm_with_history(system, history, user)` | 대화 히스토리 포함 호출 (상담 응답 생성) |

`history`는 `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]` 형태.
LangChain이 `HumanMessage` / `AIMessage`로 변환하여 GPT에 전달.

**GPT 호출 경로 2가지:**
1. LangChain 경로 (llm.py) — 상담 응답, 보고서, 사이클 정의
2. DSPy 경로 (dspy_modules/) — 총알잡기, 평가, 중립성 검사

---

## Stage 4 — 프롬프트

### 10. `config/prompts/eft_base.py` — EFT 상담 프롬프트 빌더

프로젝트의 심장. GPT 시스템 프롬프트 전체가 여기서 조립됨.

**`build_system_prompt()` 구조:**

```
[역할 및 페르소나] — "수석 EFT 커플 상담사"
[핵심 배경 지식 — 성인 애착 유형] — 4유형 설명
[핵심 배경 지식 — EFT란] — 1차/2차 정서, 교정적 정서 경험
[핵심 개입 기법] — Reflection, Validation, Evocative Responding 등 6가지
[EFT 과정 목표] — Vague→Vivid, General→Specific 등 7가지 전환
[의사소통 기법] — 핵심 메시지 반복, 내담자 어휘 활용 등
─── 여기부터 커플별 맞춤 ───
[현재 내담자 정보] — 애착 유형, ECR-R 점수, MBTI
[MBTI 보조 힌트] — F/T/I/E/S/N/J/P 별 상담 전략 (반영 강도 0.2)
[커플 결합 분류] — 32분류 공통 맥락
[당신의 32분류 렌즈] — 여성/남성 전용 상담 설명
[개입 강도 지침] — ECR-R 기반 LOW/MID/HIGH
[⚠ IPV 위험 경고] — 공포회피형일 때만
[KoELECTRA 감성 분석 결과] — 있을 때만
[⚡ 총알잡기 감지] — 방어적 발화 감지 시만
[EFT 3단계 9스텝] — 현재 단계 + 진행도
[배신감 발생 시 특별 처리]
[상담사 행동 지침]
[출력 형식 금지 규칙]
```

위쪽은 고정(EFT 이론), 아래쪽은 동적(커플마다 다름).

**`build_user_message()`** — 매 라운드 GPT에게 보내는 지시:
- 라운드 1: 상대방 입장 전달 + 감정 탐색 질문
- 새 단계: 단계에 맞는 질문
- 일반: 상대방 답변 전달 + 1차 정서 탐색 질문
- 매번 출력 금지 규칙 반복 첨부

**`build_cycle_explore_prompt()`** — 사이클 탐색 질문 생성
**`build_cycle_definition_prompt()`** — 양측 기록으로 사이클 정의
**`build_report_prompt()`** — 최종 보고서 (4섹션: 감정정리 / 파트너이해 / 중재안 / 추천대화법)

### 11. `config/prompts/stage_prompts.py` — 단계별 신호 + 진행 평가 + 총알잡기 프롬프트

**신호(Signals) 시스템 — EFT 진행 체크리스트:**

| 단계 | 신호 | 의미 |
|---|---|---|
| 1단계 | emotion | 1차 감정 어휘 직접 사용 |
| 1단계 | patternAware | 자신의 반응 패턴 인식 |
| 1단계 | otherSide | 상대 행동의 이면 언급 |
| 1단계 | relationConcern | 관계 자체에 대한 걱정 |
| 2단계 | vulnerability | 취약성 직접 표현 |
| 2단계 | empathy | 상대에 대한 연민 |
| 2단계 | recoveryWill | 관계 회복 의지 |
| 2단계 | newComm | 새로운 소통 시도 |

**SignalState**: 양측(f/m) 각 8개 신호의 bool 상태. `merge()`로 누적 — 한 번 True된 신호는 False로 돌아가지 않음.

**`build_stage_instruction()`** — 신호 상태 기반 9스텝 자동 결정:
- 1단계: 신호 0개→Step1, 1개→Step2, 2개→Step3, 3개+→Step4
- 2단계: 신호 0개→Step5, 1개→Step6, 2개+→Step7
- 3단계: Step 8~9 고정
- 미확인 신호를 GPT에게 "이걸 이끌어내라"고 구체적으로 지시

**`build_eval_prompt()`** — EFT 진행도 평가. GPT가 JSON으로 신호 판단 + stage 결정 + progress 반환.
규칙: 1→2 전진은 코드가 직접 처리(사이클 동의 필요), 단계 후퇴 없음, 신호 누적 기준.

**`build_bullet_detect_prompt()`** — 총알잡기 기본 프롬프트:
- Reactive (1단계 방어): 비난/공격/분노
- Mistrust (2단계 방어): 냉소/불신/관계 포기
- None: 일반 발화
- `suggested_intervention`: Politely Blocking / Closed Validation / Open Validation & Reframing / Parts Talk / Reality Check & Organizing

### 12. `config/prompts/neutrality_check.py` — 중립성 검사 기본 프롬프트

DSPy 최적화 파일 없을 때 사용되는 기본 프롬프트.
`build_neutrality_prompt()`로 양측 발화 + AI 응답 + 커플 정보를 조합하여 중립성 채점 프롬프트 생성.

### 13. `services/report_service.py` — 최종 보고서 생성

`generate_report()` — 여성/남성 보고서를 `asyncio.gather()`로 병렬 생성.
- `build_system_prompt(eft_stage=3, stage_progress=100)` — 32분류 렌즈가 보고서에도 반영
- `build_report_prompt()` — 4섹션 양식
- `call_llm_with_history(history=[])` — 보고서 프롬프트 자체에 기록 전문 포함

---

## Stage 5 — DSPy

### 14. `services/dspy_modules/__init__.py` — DSPy 전역 설정

`ensure_configured()` — DSPy LM 최초 1회 초기화 (싱글턴).
`dspy.LM("openai/gpt-4o-mini")` — 평가/감지용 경량 모델, `cache=False` (매번 새 호출).

### 15~17. DSPy 모듈 3개 — 듀얼 패스 구조

**공통 구조** (bullet_module.py, eval_module.py, neutrality_module.py 동일):

```
__init__():
  ensure_configured()          # DSPy LM 설정
  self.predict = dspy.Predict(Signature)
  self.is_optimized = False
  self._load_latest()          # dspy_optimized/에서 최신 v*.json 자동 로드

forward():
  if self.is_optimized:
    → _dspy_forward()          # 경로 A: DSPy 자동 프롬프트
  else:
    → _default_forward()       # 경로 B: 기존 수작업 프롬프트

_validate() / _to_dict():      # 공통 검증 (범위 클램핑, 타입 강제)
```

**현재**: 최적화 파일 없음 → 기존 프롬프트 경로(경로 B)로 동작.
**차후**: `dspy_optimized/{module}/v*.json` 파일 추가 시 자동으로 DSPy 경로(경로 A)로 전환.

| 모듈 | 하는 일 | 기본 프롬프트 출처 | 파싱 실패 시 |
|---|---|---|---|
| `bullet_module.py` | 방어적 발화 감지 | `stage_prompts.py` | 총알 없음 처리 |
| `eval_module.py` | 6개 척도 품질 채점 | 인라인 eval_prompt | 3.0점 (중간값) |
| `neutrality_module.py` | 중립성 검사 | `neutrality_check.py` | 4.0점 (통과 처리) |

**DSPy Signature**: "이런 입력 → 이런 출력" 계약. docstring에 평가 기준이 포함되어 DSPy 최적화 시 활용됨.

**버전 관리**: `dspy_optimized/{module}/v1.json, v2.json, ...` — `sorted().glob("v*.json")[-1]`로 최신 자동 로드. 이전 버전은 롤백용 보존.

---

## Stage 6 — 그래프

### 18. `graphs/neutrality_graph.py` — 중립 검사 (2노드)

```
node_llm_judge → node_verdict
```

- `node_llm_judge`: DSPy NeutralityJudge로 채점 (score, bias_direction, violations)
- `node_verdict`: 점수 기반 판정 (LLM 없음)
  - score >= 4.0 → clean pass
  - 3.0 <= score < 4.0 → pass + warning_hint (다음 라운드 주의)
  - score < 3.0 → fail → regen_triggered=True (응답 재생성)

`run_neutrality_check()` — eft_graph.py에서 직접 호출하는 함수 (LangGraph 서브그래프 아닌 함수 호출).

### 19. `graphs/eft_graph.py` — 상담 AI 핵심 그래프

**EFTState** — 50개 가까운 필드를 담은 TypedDict. 한 라운드의 모든 데이터가 이 State를 통해 노드 사이를 이동.

**7개 노드 실행 순서:**

```
risk_gate → bullet_detector → emotion_inject → eft_stage_router
→ response_generator → neutrality_check → self_refine → stage_transition_check
```

| 노드 | 역할 | LLM | 비고 |
|---|---|---|---|
| ① risk_gate | 위험 키워드 매칭 | 없음 | 자해/자살/폭행/스토킹/데이트폭력. 감지 시 즉시 END |
| ② bullet_detector | 총알잡기 (DSPy) | gpt-4o-mini | f+m 병렬 (`asyncio.gather` + `run_in_executor`) |
| ③ emotion_inject | KoELECTRA 감성 분석 | 없음 (로컬 모델) | f+m 병렬. 실패 시 graceful fallback (상담 계속) |
| ④ eft_stage_router | 단계 라운드 카운트 | 없음 | stage_rounds 업데이트 |
| ⑤ response_generator | GPT 상담 응답 생성 | gpt-4o | f+m 병렬 (편향 방지 핵심). 모든 컨텍스트 합산 |
| ⑤.5 neutrality_check | 중립성 검사 (DSPy) | gpt-4o-mini | 실패 시 ⑤로 루프백 |
| ⑥ self_refine | 6개 척도 채점 (DSPy) | gpt-4o-mini | 미달 시 ⑤로 루프백. 최대 3회. safety 하드게이트 |
| ⑦ stage_transition_check | 단계 전환 판단 | gpt-4o-mini | 신호 누적 + 진행도. 1→2는 사이클 동의 필요 |

**재생성 루프:**
```
response_generator → neutrality_check ──실패──→ response_generator
                          │
                        통과
                          ↓
                     self_refine ──미달──→ response_generator
                          │
                        통과
                          ↓
                  stage_transition_check → END
```

최대 `MAX_REFINE(3)`회까지 재생성.

**response_generator 프롬프트 합산:**
- build_system_prompt(): 32분류 + 개입 지침 + IPV 경고 + 총알잡기 컨텍스트 + 감성 분석 결과
- build_user_message(): 단계 지시 + 상대방 발화 + Self-Refine 피드백 + 중립성 교정 피드백

**stage_transition_check 규칙:**
- 단계 후퇴 없음: `max(stage, new_stage)`
- 1→2: 사이클 동의 절차가 필요하므로 여기서 차단
- 2→3: 양측 모두 2단계 신호 2개 이상 + MIN_STAGE_ROUNDS 충족
- needs_cycle_definition: 양측 1단계 신호 각 1개 이상이면 True

**위험 키워드 (하드코딩):**
- 자해: 자해, 손목, 긋
- 자살: 자살, 죽고 싶, 뛰어내리, 목매, 극단적 선택
- 폭행: 때리, 죽이, 폭행, 칼로, 협박
- 스토킹: 스토킹, 신상, 감금, 따라다
- 데이트폭력: 강제로, 강간, 성폭
- IPV 커플 추가: 무서워, 두려워, 겁나, 맞아, 밀쳐

---

## Stage 7 — 상담 라우터

### 20. `routers/counseling.py` — Spring 연동 최종 접점

| 엔드포인트 | 메서드 | 설명 |
|---|---|---|
| `/counseling/session` | POST | 세션 생성 (ECR-R → 32분류 프로파일 조립 → 세션 저장) |
| `/counseling/round` | POST | 라운드 실행 (세션 복원 → 그래프 실행 → 히스토리 누적 → 세션 업데이트) |
| `/counseling/cycle` | POST | 사이클 탐색/정의/동의 (3가지 분기) |
| `/counseling/end` | POST | 3단계 종료 동의 |
| `/counseling/report` | POST | 최종 보고서 (여성/남성 병렬 생성) |
| `/counseling/session/{id}` | DELETE | 세션 삭제 |

**`/counseling/round` 핵심 흐름:**
1. `session_get()` → 세션에서 상태 복원
2. `create_initial_state()` → LangGraph State 조립
3. `graph.ainvoke(state)` → 7개 노드 순차 실행
4. 히스토리 누적: `[{role: "user", content: 발화}, {role: "assistant", content: AI응답}]`
5. `session_set()` → 세션 업데이트
6. `CounselingRoundResponse` 반환

**`/counseling/cycle` 3가지 분기:**
1. 양측 동의 (f_agreed + m_agreed) → eft_stage=2, CycleAgreedResponse
2. cycle_definition 없음 → 탐색 질문 생성 (GPT), CycleExploreResponse
3. cycle_definition 있음 → 사이클 정의 생성 (GPT), CycleDefinitionResponse

**Spring 관점 API 호출 순서:**
```
1. POST /counseling/session       → 세션 생성
2. POST /counseling/round (반복)  → 1단계 상담
   ← needs_cycle_definition=True
3. POST /counseling/cycle         → 탐색 질문 → 사이클 정의 → 동의 → 2단계 진입
4. POST /counseling/round (반복)  → 2~3단계 상담
5. POST /counseling/end           → 종료 동의
6. POST /counseling/report        → 보고서 수신
7. DELETE /counseling/session/{id} → 세션 정리
```

---

## 부록: 기술 패턴 요약

### 비동기 병렬 처리

```python
# 여성/남성 응답 동시 생성 (편향 방지 + 속도 2배)
f_resp, m_resp = await asyncio.gather(
    call_llm_with_history(f_sys, f_hist, f_user_msg),
    call_llm_with_history(m_sys, m_hist, m_user_msg),
)

# DSPy 동기 함수를 비동기 환경에서 실행
result = await loop.run_in_executor(None, lambda: detector.forward(...))
```

### 싱글턴 패턴

`EmotionService`, `BulletDetector`, `EFTEvaluator`, `NeutralityJudge`, `_eft_graph` — 모두 싱글턴.
모델/그래프 로딩이 무거우므로 최초 1회만 생성, 이후 재사용.

### DSPy 듀얼 패스

```
최적화 파일 있음? → _dspy_forward() (DSPy 자동 프롬프트)
없음?            → _default_forward() (기존 수작업 프롬프트)
```

현재는 전부 기본 경로. 데이터 축적 후 최적화 실행하면 자동 전환.

### 방어적 코딩

- JSON 파싱 실패 → 안전한 기본값 반환 (서비스 중단 방지)
- KoELECTRA 실패 → None으로 graceful fallback (상담 계속)
- GPT 이상한 값 → 범위 클램핑 (score 1~5, confidence 0~1)
- 중립성 검사 오류 → 통과 처리 (5.0점)

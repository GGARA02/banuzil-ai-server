# LangGraph 파이프라인 — EFT 상담 그래프 정리

> 대상 코드: [`graphs/eft_graph.py`](../graphs/eft_graph.py), [`graphs/neutrality_graph.py`](../graphs/neutrality_graph.py)
> 진입점: `POST /ai/round-analyze` → `get_eft_graph().ainvoke(state)` ([routers/counseling.py](../routers/counseling.py))
> 작성일: 2026-05-29

이 문서는 한 라운드(양측 발화 1쌍)가 들어왔을 때 AI 서버 내부에서 실행되는
**LangGraph 상태 그래프**의 전체 흐름을 정리한 것이다.

---

## 한눈에 보는 그래프

```
                      [ POST /ai/round-analyze ]
                                  │
                    fetch_session_context (DB 조회)
                                  │
                       create_initial_state
                                  │
                                  ▼
        ┌──────────────────── EFT GRAPH ────────────────────┐
        │                                                    │
        │   risk_gate                                        │
        │      │ (항상 진행 — 위험 감지해도 중단 안 함)        │
        │      ▼                                              │
        │   bullet_detector        (DSPy, 총알잡기 감지)      │
        │      ▼                                              │
        │   emotion_inject         (KoELECTRA 감성 분석)      │
        │      ▼                                              │
        │   eft_stage_router       (단계/사이클 진입 판단)     │
        │      ▼                                              │
        │   response_generator ◄──────────┐ (f+m 병렬 생성)   │
        │      ▼                           │                  │
        │   neutrality_check ──────────────┤ (편향 시 루프백)  │
        │      │                           │                  │
        │      ▼                           │                  │
        │   self_refine ───────────────────┘ (미달 시 루프백)  │
        │      ▼                                              │
        │   stage_transition_check (신호 누적 + 단계 전환)     │
        │      ▼                                              │
        │     END                                            │
        └────────────────────────────────────────────────────┘
                                  │
                       save_round_result (DB 저장)
                                  │
                  RoundAnalyzeResponse → Spring
```

핵심 설계 원칙:

- **f(여성) + m(남성) 동시 처리**: 모든 LLM·모델 호출은 `asyncio.gather`로 병렬 실행해 한쪽으로 쏠리는 편향을 막는다.
- **단계 후퇴 없음**: EFT 단계(1→2→3)는 코드가 직접 통제하며 절대 내려가지 않는다 (`new_stage = max(stage, ...)`).
- **graceful fallback**: 보조 모델(KoELECTRA, 중립 검사 등)이 실패해도 상담은 중단되지 않는다.

---

## State: `EFTState`

그래프 전 노드가 공유하는 단일 dict 상태. 주요 필드만 발췌 ([eft_graph.py:67](../graphs/eft_graph.py)).

| 그룹 | 필드 | 설명 |
|------|------|------|
| 세션 | `session_id`, `couple_profile` | 세션 ID, 커플 애착 프로필 |
| 입력 | `f_reply`, `m_reply`, `round_num` | 이번 라운드 양측 발화 |
| 히스토리 | `f_history`, `m_history` | `[{role, content}]` 누적 대화 |
| EFT 상태 | `eft_stage`, `stage_rounds`, `stage_progress`, `signals` | 단계(1/2/3), 단계별 라운드 수, 진행도(0~100), 신호 누적 |
| 사이클 | `cycle_definition`, `cycle_skip_until`, `is_cycle_round`, `needs_cycle_definition` | 사이클 정의문 / 거절 스킵 / 진입 라운드 / 정의 필요 플래그 |
| 감성 | `f_emotion_data`, `m_emotion_data` | KoELECTRA 분석 결과 |
| 총알잡기 | `bullet_detected`, `bullet_type`, `bullet_target` | 공격 발화 감지 결과 |
| 생성 | `f_response`, `m_response` | 최종 상담사 응답 |
| 품질 | `refine_count`, `eval_scores`, `refine_feedback`, `neutrality_result` | Self-Refine / 중립 검사 |
| 위험 | `risk_flag`, `risk_category`, `risk_keywords_found` | 위험 키워드 감지 |
| RAG | `rag_context` | 과거 동일 커플 세션 참고 컨텍스트 |

---

## 노드별 상세

### 1. `risk_gate` — 위험 키워드 게이트 (LLM 없음)

- `f_reply + m_reply`를 **하드코딩 키워드**(`_RISK_KEYWORDS_HARD`)로 순수 문자열 매칭. 자해/자살/폭행/스토킹/데이트폭력 카테고리.
- 공포회피형 결합(`ipv_risk_flag`)이면 추가 데이트폭력 키워드까지 확장 검사.
- **감지해도 상담을 멈추지 않는다.** `risk_flag`만 state에 기록 → 최종 응답에 실어 Spring이 별도 처리.
- 엣지: 항상 `bullet_detector`로 진행.

### 2. `bullet_detector` — 총알잡기 감지 (DSPy)

- `BULLET_DETECTION_ENABLED`로 ON/OFF. 꺼져 있으면 즉시 `bullet_detected=False` 반환.
- DSPy `BulletDetector.forward()`(동기)를 `run_in_executor`로 **f/m 병렬** 실행.
- `confidence >= BULLET_THRESHOLD(0.9)`인 경우만 총알로 판정 → 확실한 공격에만 발동.
- 출력: `bullet_type`(Reactive/Mistrust/None), `bullet_target`(f/m), `bullet_intervention`(권장 개입).
- 감지 시 `response_generator`에서 5단계 총알잡기 프로토콜 컨텍스트로 주입된다.

### 3. `emotion_inject` — KoELECTRA 감성 분석

- `EmotionService`를 직접 임포트(별도 HTTP 없음), `run_in_executor`로 f/m 병렬 분석.
- 결과(`category`, `detail`)를 `f_emotion_data`/`m_emotion_data`에 저장.
- **실패해도 None으로 graceful fallback** — 상담 중단 없음.
- `_format_emotion_context()`로 프롬프트 삽입용 텍스트(대분류 2개 + 소분류 3개)로 변환되어 응답 생성에 쓰인다.

### 4. `eft_stage_router` — 단계/사이클 진입 판단 (LLM 없음)

응답 생성 **전에** 이번 라운드의 성격을 미리 결정한다.

- `stage_rounds[현재 단계]`를 +1 (라운드 카운트 증가).
- **사이클 진입 판단** (`is_cycle_round`) — `eft_stage==1` 이고 `cycle_definition`이 비어 있을 때만:
  - `round_num >= cycle_skip_until` (직전에 거절해 스킵 중이 아님) 그리고
  - 정상 경로: 양측 모두 1단계 신호 2개 이상 (`f_cnt>=2 and m_cnt>=2`), **또는**
  - 안전 게이트: 1단계가 `CYCLE_FORCE_ROUNDS(3)` 이상 길어지면 신호 미달이어도 진입(철회형 정체 방지).
- 진입 라운드면 `cycle_skip_until = round_num + 3` 세팅 (거절 시 2라운드 자동 스킵용).

### 5. `response_generator` — 양측 응답 동시 생성 (핵심 노드)

파이프라인의 중심. f/m 각각의 시스템 프롬프트 + 유저 메시지를 조립해 **병렬 GPT 호출**.

조립되는 컨텍스트:
- `build_system_prompt`: 성별, 커플 프로필, 단계, 진행도 + 아래 컨텍스트들
  - **총알잡기 컨텍스트** (`bullet_detected` 시)
  - **감성 컨텍스트** (KoELECTRA 결과)
  - **RAG 컨텍스트** (`rag_context` — 사이클 정의 시점에 검색되어 주입됨)
- `build_stage_instruction`: 단계별 지침 (3단계는 Step 8/9 분화 위해 누적 라운드 전달)
- 파트너 발화 전달: 여성에겐 `m_reply`, 남성에겐 `f_reply`
- **루프백 피드백**: `refine_feedback`(Self-Refine 미달) + `neutrality_fb`(중립 위반) + `warning_hint`(전 라운드 경고)가 있으면 지침 끝에 덧붙임

```python
f_resp, m_resp = await asyncio.gather(
    _call_llm_with_history(f_sys, f_hist, f_user_msg, ...),
    _call_llm_with_history(m_sys, m_hist, m_user_msg, ...),
)
```

이 노드는 `neutrality_check` 또는 `self_refine`에서 **재진입(루프백)**될 수 있다. 재진입 시 `refine_feedback`은 초기화된다.

### 5.5. `neutrality_check` — 중립성 검사 (서브 파이프라인)

[`neutrality_graph.py`](../graphs/neutrality_graph.py)의 `run_neutrality_check()`를 직접 호출(LangGraph 서브그래프 대신 노드 함수 순차 실행).

2개 노드로 구성:
- **`llm_judge`** (DSPy `NeutralityJudge`): 이번 라운드 발화+응답의 편향을 채점 → `neutrality_score`(0~5), `bias_direction`(toward_f / toward_m / none), `violations`.
- **`verdict`** (LLM 없음): 점수 기반 판정.
  - `score >= 4(WARN)` → clean pass
  - `3(PASS) <= score < 4` → pass + `warning_hint`(다음 라운드 프롬프트에 반영)
  - `score < 3` → **fail → `regen_triggered=True`**

엣지 `edge_neutrality_check`:
- `max_refine == 0` → `stage_transition_check` (검사·재생성 스킵)
- `regen_triggered && refine_count < max_refine` → **`response_generator` 루프백**
- 그 외 → `self_refine`

> 입력은 이번 라운드 발화/응답 + `couple_profile`만 사용 (히스토리 없음). 실패 시 통과 처리.

### 6. `self_refine` — 6개 척도 채점 + 재생성 (DSPy)

DSPy `EFTEvaluator.forward()`(동기, `run_in_executor`)로 응답 품질을 6개 척도로 채점.

| 척도 | 가중치 |
|------|--------|
| neutrality | 0.30 |
| safety | 0.20 |
| validation_depth | 0.15 |
| attach_coherence | 0.13 |
| cycle_reframing | 0.12 |
| actionability | 0.10 |

가중 평균(`weighted_avg`)을 계산 후 재생성 조건 판정:
- **안전 게이트**: `safety < SAFETY_GATE_SCORE(3.0)` → 무조건 재생성
- **품질 미달**: `weighted_avg < EVAL_PASS_SCORE(3.5)` 이고 `refine_count < MAX_REFINE(3)` → 재생성

재생성이면 미달 척도 + 개선 방향을 `refine_feedback`에 담아 엣지 `edge_self_refine`가 `response_generator`로 루프백. 통과면 `stage_transition_check`로 진행.

### 7. `stage_transition_check` — 신호 누적 + 단계 전환

EVAL 모델(`EVAL_MODEL_NAME`)로 진행도를 평가하고 JSON 파싱 → 신호를 누적(`signals.merge`, 한 번 True면 유지).

**단계 전환은 코드가 직접 통제** (eval 모델이 함부로 올리지 못함):
- **1→2**: `cycle_definition`이 존재하고 1단계 누적 라운드 `>= MIN_STAGE_ROUNDS(2)`일 때만. (별도 "동의" 통로가 없으므로 사이클 정의 존재를 트리거로 사용)
- **2→3**: eval이 신호 기반 3 제안 + 최소 라운드 충족, **또는** `STAGE2_FORCE_ROUNDS(4)` 누적 + 양측 깊은 신호 합 `>= 2`(정체 방지 강제 진입).
- **단계 후퇴 없음**: `new_stage = max(stage, min(3, ...))`.

**진행도(`stage_progress`) 계산**:
- 1·2단계: 해당 단계 신호(4종×2명=8칸) 누적 개수 / 8 × 100, 최대 90.
- 3단계: Step 8(s3<3) 구간 75 이하로 묶어 조기 종료 방지 → Step 9(s3>=3)부터 90~100으로 상승. **3단계 + progress>=90 도달 시 Spring이 종료 + 보고서 생성.**
- `needs_cycle_definition`: `eft_stage_router`의 `is_cycle_round` 결과를 그대로 전달 → Spring이 `/ai/cycle` 절차 시작.

---

## 그래프 외부 흐름 (라운드 전후)

| 단계 | 함수 | 역할 |
|------|------|------|
| 전 | `fetch_session_context` | Supabase에서 세션·유저·애착·히스토리 조회, gender로 f/m 판별 ([session_service.py:31](../services/session_service.py)) |
| 전 | `create_initial_state` | 조회 결과로 `EFTState` 초기화 |
| — | `graph.ainvoke(state)` | 위 그래프 실행 |
| 후 | `save_round_result` | `mediation_records.ai_response` UPDATE + `mediation_sessions` EFT 상태 UPDATE (current_round+1 등) |
| 후 | `RoundAnalyzeResponse` | f_message/m_message + 플래그(needs_cycle, risk, stage, progress)를 Spring에 반환 |

---

## 설정값 요약 (`config/settings.py`)

| 상수 | 기본값 | 의미 |
|------|--------|------|
| `MODEL_NAME` | gpt-4o | 상담 응답 생성 모델 |
| `EVAL_MODEL_NAME` | gpt-4o-mini | 평가/진행도/중립 검사 경량 모델 |
| `TEMPERATURE` | 0.7 | 생성 온도 |
| `BULLET_THRESHOLD` | 0.9 | 총알 판정 confidence 하한 |
| `MAX_REFINE` | 3 | Self-Refine 최대 재생성 |
| `EVAL_PASS_SCORE` | 3.5 | 가중 평균 통과 기준 |
| `SAFETY_GATE_SCORE` | 3.0 | 안전성 강제 재생성 기준 |
| `NEUTRALITY_PASS_SCORE` / `WARN` | 3.0 / 4.0 | 중립 검사 통과 / 경고 기준 |
| `MIN_STAGE_ROUNDS` | 2 | 단계 전환 최소 누적 라운드 |
| `STAGE2_FORCE_ROUNDS` | 4 | 2→3 강제 진입 라운드 (코드 상수) |
| `CYCLE_FORCE_ROUNDS` | 3 | 1단계 사이클 강제 진입 라운드 (코드 상수) |
| `RISK_DETECTION_THRESHOLD` / `_IPV` | 0.85 / 0.70 | 위험 감지 임계값 (IPV 결합 시 민감) |
| `RAG_ENABLED` / `RAG_SIMILARITY_THRESHOLD` | true / 0.55 | RAG 사용 / 채택 유사도 하한 |

---

## 참고: 사이클 정의는 이 그래프 밖에서 처리

`/ai/cycle`(탐색 질문·사이클 정의 생성)은 위 LangGraph를 거치지 않고
[counseling.py](../routers/counseling.py)에서 직접 LLM을 호출한다.
본 그래프는 `cycle_definition`의 **존재 여부**만 1→2 전환 트리거로 읽는다.
사이클 정의 절차의 상세는 [SPRING_MIGRATION_CYCLE_BRIDGE.md](SPRING_MIGRATION_CYCLE_BRIDGE.md) 참고.

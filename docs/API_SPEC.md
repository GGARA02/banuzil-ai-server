# 바느질 AI 서버 — API 명세서 v3.0 (Spring 연동용)

## 기본 정보

| 항목 | 값 |
|------|----|
| Base URL | `http://localhost:8000` (운영 환경에 맞게 변경) |
| Content-Type | `application/json` |
| 인증 | 없음 (내부 서버 간 통신) |
| 인코딩 | UTF-8 |

---

## v3.0 핵심 변경 — Stateless 전환

v2에서 AI 서버가 메모리에 세션 상태를 관리했으나, v3에서는 **AI 서버가 상태를 보유하지 않는다**.

| 항목 | v2 (Stateful) | v3 (Stateless) |
|------|---------------|----------------|
| 세션 관리 | AI 서버 메모리 | **Spring DB** |
| 히스토리 | AI 서버에 누적 | **Spring이 매 요청에 전달** |
| EFT 상태 | AI 서버에 저장 | **Spring이 매 요청에 전달** |
| 세션 생성/삭제 | AI 서버 엔드포인트 | **Spring이 직접 DB 처리** |
| 종료 동의 | AI 서버 엔드포인트 | **Spring이 직접 DB 플래그 관리** |

**Spring이 해야 할 일**: 매 요청에 필요한 모든 컨텍스트(히스토리, EFT 상태, 프로필)를 보내고, 응답의 `updated_*` 필드를 DB에 저장하여 다음 요청에 재전달.

---

## 상담 세션 전체 흐름

```
[Spring]                               [AI 서버]
   │
   ├─ 세션 생성 (Spring DB)
   ├─ 양측 입력 수집
   │
   ├── POST /ai/round-analyze ──────►  EFT 분석 + 응답 생성
   │   (프로필 + 히스토리 + 상태)        │
   │◄── 응답 + updated_* 필드 ─────────┘
   │
   ├─ updated_* 를 DB에 저장
   │
   ├─ needs_cycle_definition: true?
   │         │
   │         ▼
   │  POST /ai/cycle ────────────────►  사이클 탐색/정의
   │         │
   │  동의 처리는 Spring이 직접 관리
   │
   ├─ (라운드 반복)
   │
   ├─ 3단계 종료 동의 (Spring이 직접 관리)
   │
   └── POST /ai/report ─────────────►  최종 보고서 생성
```

### Spring이 처리해야 할 분기

| 응답 필드 | 값 | Spring 처리 |
|-----------|-----|------------|
| `risk_flag` | `true` | 상담은 계속 진행됨. Spring이 위험 신호를 별도로 처리 (전문기관 연계 안내 등) |
| `needs_cycle_definition` | `true` | `POST /ai/cycle` 호출 |
| EFT 3단계 도달 + 양측 종료 동의 | (Spring DB 관리) | `POST /ai/report` 호출 |

---

## 엔드포인트 목록

| # | Method | Path | 설명 |
|---|--------|------|------|
| 1 | POST | `/ai/round-analyze` | 라운드 분석 (핵심) |
| 2 | POST | `/ai/cycle` | 사이클 탐색/정의 |
| 3 | POST | `/ai/report` | 최종 보고서 생성 |
| 4 | POST | `/emotion/analyze` | 단일 발화 감성 분석 (테스트/독립 호출용) |
| 5 | POST | `/emotion/analyze/batch` | 일괄 감성 분석 |
| 6 | GET | `/emotion/health` | 서버 상태 확인 |

---

## 1. POST /ai/round-analyze — 라운드 분석

매 라운드마다 호출. Spring이 모든 컨텍스트를 보내면 AI가 분석 후 응답 + 업데이트된 상태를 반환한다.

### Request Body

| 필드 | 타입 | 필수 | 제약 | 설명 |
|------|------|------|------|------|
| `session_id` | string | ✅ | UUID 권장 | Spring이 관리하는 세션 식별자 |
| `f_reply` | string | ✅ | 최대 500자 | 여성 내담자 현재 발화 |
| `m_reply` | string | ✅ | 최대 500자 | 남성 내담자 현재 발화 |
| `f_anxiety` | float | ✅ | 1.0 ~ 7.0 | 여성 ECR-R 불안 점수 |
| `f_avoidance` | float | ✅ | 1.0 ~ 7.0 | 여성 ECR-R 회피 점수 |
| `f_mbti` | string | ❌ | 예: "ENFP" | 여성 MBTI |
| `m_anxiety` | float | ✅ | 1.0 ~ 7.0 | 남성 ECR-R 불안 점수 |
| `m_avoidance` | float | ✅ | 1.0 ~ 7.0 | 남성 ECR-R 회피 점수 |
| `m_mbti` | string | ❌ | 예: "ISTJ" | 남성 MBTI |
| `f_history` | array | ❌ | — | 여성 대화 히스토리 (Spring DB에서 전달) |
| `m_history` | array | ❌ | — | 남성 대화 히스토리 |
| `eft_stage` | integer | ❌ | 1~3 | 현재 EFT 단계 (기본 1) |
| `round_num` | integer | ❌ | 1~ | 현재 라운드 번호 (기본 1) |
| `stage_rounds` | object | ❌ | — | 단계별 누적 라운드 `{1:0, 2:0, 3:0}` |
| `stage_progress` | integer | ❌ | 0~100 | 현재 단계 진행도 |
| `signals` | object | ❌ | — | EFT 신호 상태 `{"f":{...}, "m":{...}}` |
| `cycle_definition` | string | ❌ | — | 사이클 정의 텍스트 |
| `cycle_skip_until` | integer | ❌ | 0 | 사이클 거부 시 재시도 기준 라운드 (0이면 즉시 가능) |
| `model_name` | string | ❌ | — | LLM 오버라이드 |
| `bullet_enabled` | boolean | ❌ | — | 총알잡기 ON/OFF 오버라이드 |
| `emotion_weight` | float | ❌ | 0.0~1.0 | 감성 반영 강도 오버라이드 |
| `max_refine` | integer | ❌ | 3 | Self-Refine 최대 횟수 (0이면 평가 스킵) |

> **히스토리 형식**: `[{"role": "user", "content": "발화"}, {"role": "assistant", "content": "AI응답"}, ...]`
> 첫 라운드에서는 빈 배열 `[]`을 보내면 된다.

### Request 예시 — 첫 라운드

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_reply": "상담사님, 파트너한테 연락했는데 한참 뒤에 짧게 답장이 왔어요. 무시당하는 느낌이에요.",
  "m_reply": "오늘 일이 많아서 답장이 늦었거든요. 파트너가 화가 난 것 같아서 어떻게 해야 할지 모르겠어요.",
  "f_anxiety": 4.5, "f_avoidance": 2.1, "f_mbti": "ENFP",
  "m_anxiety": 1.8, "m_avoidance": 4.2, "m_mbti": "ISTJ",
  "f_history": [], "m_history": [],
  "eft_stage": 1, "round_num": 1,
  "stage_rounds": {"1": 0, "2": 0, "3": 0},
  "stage_progress": 0, "signals": null, "cycle_definition": ""
}
```

### Request 예시 — 2라운드 이후

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_reply": "맞아요, 무시당하는 느낌보다는 사실 불안한 거였어요.",
  "m_reply": "파트너가 불안해한다는 걸 이제 알겠어요.",
  "f_anxiety": 4.5, "f_avoidance": 2.1, "f_mbti": "ENFP",
  "m_anxiety": 1.8, "m_avoidance": 4.2, "m_mbti": "ISTJ",
  "f_history": [
    {"role": "user", "content": "상담사님, 파트너한테 연락했는데..."},
    {"role": "assistant", "content": "연락이 없었던 시간 동안 얼마나 불안하셨을지..."}
  ],
  "m_history": [
    {"role": "user", "content": "오늘 일이 많아서 답장이 늦었거든요..."},
    {"role": "assistant", "content": "바빴던 건 사실이지만, 파트너가 그렇게 느꼈다는 걸..."}
  ],
  "eft_stage": 1, "round_num": 2,
  "stage_rounds": {"1": 1, "2": 0, "3": 0},
  "stage_progress": 35,
  "signals": {
    "f": {"emotion": true, "patternAware": false, "otherSide": false, "relationConcern": true, "vulnerability": false, "empathy": false, "withdrawer_reengagement": false, "blamer_softening": false},
    "m": {"emotion": false, "patternAware": false, "otherSide": true, "relationConcern": false, "vulnerability": false, "empathy": false, "withdrawer_reengagement": false, "blamer_softening": false}
  },
  "cycle_definition": ""
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 ID |
| `f_message` | string | **여성에게 전달할 AI 상담사 메시지** |
| `m_message` | string | **남성에게 전달할 AI 상담사 메시지** |
| `updated_eft_stage` | integer | **DB에 저장할 EFT 단계** |
| `updated_stage_rounds` | object | **DB에 저장할 단계별 라운드 수** |
| `updated_stage_progress` | integer | **DB에 저장할 진행도** |
| `updated_signals` | object | **DB에 저장할 신호 상태** |
| `updated_f_history` | array | **DB에 저장할 여성 히스토리** (현재 라운드 포함) |
| `updated_m_history` | array | **DB에 저장할 남성 히스토리** (현재 라운드 포함) |
| `needs_cycle_definition` | boolean | `true`면 `/ai/cycle` 호출 필요. 이 라운드 응답은 질문 없이 감정 전달만으로 마무리됨 |
| `cycle_skip_until` | integer | 사이클 거부 시 Spring이 저장할 재시도 라운드. 다음 요청에 재전달 |
| `risk_flag` | boolean | `true`면 위험 신호 감지 — 상담은 계속 진행, Spring이 별도 처리 |
| `risk_category` | string | 위험 유형 ("자해", "자살", "폭행" 등) |
| `bullet_detected` | boolean | 총알잡기 감지 여부 |
| `bullet_type` | string | `"Reactive"` / `"Mistrust"` / `"None"` |
| `eval_score` | float \| null | 응답 품질 가중평균 점수 |
| `eval_scores` | object \| null | Self-Refine 세부 점수 |
| `neutrality_result` | object \| null | 중립성 검사 결과 |
| `f_emotion` | object \| null | KcELECTRA 여성 감성 분석 |
| `m_emotion` | object \| null | KcELECTRA 남성 감성 분석 |
| `risk_keywords` | array | 감지된 위험 키워드 목록 |

> **핵심**: `updated_*` 접두사가 붙은 필드들을 DB에 저장하고, 다음 라운드 요청에 그대로 재전달하면 된다.

### Response 예시 — 정상

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_message": "연락이 없었던 시간 동안 얼마나 불안하고 외로우셨을지... 그 마음이 느껴집니다.",
  "m_message": "바빴던 건 사실이지만, 파트너가 그렇게 느꼈다는 걸 알게 되셨군요.",
  "updated_eft_stage": 1,
  "updated_stage_rounds": {"1": 1, "2": 0, "3": 0},
  "updated_stage_progress": 35,
  "updated_signals": {
    "f": {"emotion": true, "patternAware": false, "otherSide": false, "relationConcern": true, "vulnerability": false, "empathy": false, "withdrawer_reengagement": false, "blamer_softening": false},
    "m": {"emotion": false, "patternAware": false, "otherSide": true, "relationConcern": false, "vulnerability": false, "empathy": false, "withdrawer_reengagement": false, "blamer_softening": false}
  },
  "updated_f_history": [
    {"role": "user", "content": "상담사님, 파트너한테 연락했는데..."},
    {"role": "assistant", "content": "연락이 없었던 시간 동안 얼마나 불안하고 외로우셨을지..."}
  ],
  "updated_m_history": [
    {"role": "user", "content": "오늘 일이 많아서 답장이 늦었거든요..."},
    {"role": "assistant", "content": "바빴던 건 사실이지만, 파트너가 그렇게 느꼈다는 걸..."}
  ],
  "needs_cycle_definition": false,
  "risk_flag": false,
  "risk_category": "",
  "bullet_detected": false,
  "bullet_type": "None",
  "eval_score": 4.25,
  "f_emotion": {
    "category": [{"rank": 1, "label": "슬픔", "score": 0.68}],
    "detail": [{"rank": 1, "label": "외로운", "score": 0.45}]
  },
  "m_emotion": {
    "category": [{"rank": 1, "label": "당황", "score": 0.55}],
    "detail": [{"rank": 1, "label": "난감한", "score": 0.40}]
  },
  "risk_keywords": []
}
```

### Response 예시 — 위험 감지 (상담은 계속 진행)

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_message": "지금 많이 힘드신 거군요. 그 마음이 느껴집니다...",
  "m_message": "파트너가 많이 힘들어하고 있는 것 같습니다...",
  "updated_eft_stage": 1,
  "updated_stage_rounds": {"1": 1, "2": 0, "3": 0},
  "updated_stage_progress": 15,
  "updated_signals": {"f": {"emotion": true}, "m": {}},
  "updated_f_history": [...],
  "updated_m_history": [...],
  "risk_flag": true,
  "risk_category": "자해",
  "risk_keywords": ["손목", "긋"],
  "eval_score": 4.1
}
```

> **위험 감지 시 동작**: AI 서버는 위험 키워드를 감지해도 상담을 중단하지 않고 정상적인 상담 응답을 생성합니다. `risk_flag`, `risk_category`, `risk_keywords`를 통해 Spring에 위험 신호를 전달하며, Spring이 전문기관 연계 등 별도 조치를 수행해야 합니다.

### 에러

| 상태코드 | 조건 |
|---------|------|
| `422 Unprocessable Entity` | 필수 필드 누락, 값 범위 초과 등 |
| `500 Internal Server Error` | AI 처리 오류 |

---

## 2. POST /ai/cycle — 사이클 탐색/정의

`round-analyze` 응답에서 `needs_cycle_definition: true`를 받은 후 호출. 동의 처리는 Spring이 DB에서 직접 관리한다.

### Request Body

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | ✅ | 세션 ID |
| `f_history` | array | ✅ | 여성 대화 히스토리 |
| `m_history` | array | ✅ | 남성 대화 히스토리 |
| `f_explore_answer` | string | ❌ | 여성 탐색 질문 답변 (있으면 정의 생성) |
| `m_explore_answer` | string | ❌ | 남성 탐색 질문 답변 (있으면 정의 생성) |

### 호출 시나리오

```
[needs_cycle_definition: true 수신]
  → 이 라운드 AI 응답은 질문 없이 감정 전달만으로 마무리됨
  ↓
1. 답변 없이 호출 → 탐색 질문 수신 (f_question, m_question)
  ↓
2. 양측에게 질문 표시 → 답변 수집
  ↓
3. 답변 포함 호출 → 사이클 정의 수신 (cycle_definition)
  ↓
4. 양측에게 정의 표시 → 동의 여부는 Spring이 DB에서 직접 관리
  ├─ 동의 → Spring이 cycle_definition 저장 + eft_stage=2
  └─ 거부 → Spring이 cycle_skip_until = round_num + 2 저장
            → 2라운드 더 진행 후 자동 재시도
```

### 응답 케이스 2가지

**케이스 A: 탐색 질문** (답변 없이 호출)

```json
{
  "session_id": "550e8400-...",
  "f_question": "지금까지 나눈 이야기에서, 두 분 사이에 반복되는 패턴이 있다고 느끼시나요?",
  "m_question": "파트너가 연락을 요구할 때, 그 순간 내면에서 어떤 감정이 드셨나요?",
  "cycle_round": 1
}
```

**케이스 B: 사이클 정의** (답변 포함 호출)

```json
{
  "session_id": "550e8400-...",
  "cycle_definition": "여성이 연락을 요구하며 다가갈수록 남성은 압도감을 느껴 물러나고, 남성이 물러날수록 여성은 버림받는 공포에 더 강하게 다가가는 '추구-철회' 사이클이 반복되고 있습니다.",
  "message": "양측 동의를 기다립니다."
}
```

---

## 3. POST /ai/report — 최종 보고서

양측 종료 동의 후 호출. Spring이 DB에서 꺼낸 전체 히스토리 + 프로필을 전달한다. 생성에 수십 초 소요될 수 있다.

### Request Body

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | ✅ | 세션 ID |
| `f_anxiety` | float | ✅ | 여성 ECR-R 불안 |
| `f_avoidance` | float | ✅ | 여성 ECR-R 회피 |
| `f_mbti` | string | ❌ | 여성 MBTI |
| `m_anxiety` | float | ✅ | 남성 ECR-R 불안 |
| `m_avoidance` | float | ✅ | 남성 ECR-R 회피 |
| `m_mbti` | string | ❌ | 남성 MBTI |
| `f_history` | array | ✅ | 여성 전체 히스토리 |
| `m_history` | array | ✅ | 남성 전체 히스토리 |
| `cycle_definition` | string | ❌ | 사이클 정의 텍스트 |

### Request 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_anxiety": 4.5, "f_avoidance": 2.1, "f_mbti": "ENFP",
  "m_anxiety": 1.8, "m_avoidance": 4.2, "m_mbti": "ISTJ",
  "f_history": [...],
  "m_history": [...],
  "cycle_definition": "추구-철회 사이클"
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 ID |
| `f_report` | string | 여성 내담자 전용 보고서 |
| `m_report` | string | 남성 내담자 전용 보고서 |

### 에러

| 상태코드 | 조건 |
|---------|------|
| `500 Internal Server Error` | 보고서 생성 오류 |

---

## 4. POST /emotion/analyze — 단일 감성 분석

상담과 독립적으로 단일 발화의 감성을 분석한다. 테스트/디버깅용.
HierarchicalEmotionModel(concat_unweight) 단일 모델로 대분류/소분류를 동시 추론한다.
(상담 내부에서는 LangGraph 파이프라인이 EmotionService를 직접 호출하므로 이 엔드포인트를 거치지 않는다.)

### Request Body

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `text` | string | ✅ | — | 분석할 발화 문장 |
| `gender` | string | ❌ | `"미상"` | 성별 (`"여성"` / `"남성"` / `"미상"`) |
| `situation` | string | ❌ | `"연애"` | 상황 키워드 |

### Response 예시

```json
{
  "input": {"gender": "여성", "situation": "연애", "text": "왜 연락을 안 해? 화가 난다."},
  "model": "concat_unweight",
  "category": [
    {"rank": 1, "label": "분노", "score": 0.7134},
    {"rank": 2, "label": "상처", "score": 0.1823}
  ],
  "detail": [
    {"rank": 1, "label": "노여워하는", "score": 0.4231},
    {"rank": 2, "label": "짜증내는", "score": 0.2814},
    {"rank": 3, "label": "억울한", "score": 0.1502}
  ]
}
```

---

## 5. POST /emotion/analyze/batch — 일괄 감성 분석

여러 발화를 한 번에 분석한다.

### Request 예시

```json
{
  "items": [
    {"text": "파트너가 연락을 잘 안 해줘서 힘들어요.", "gender": "여성", "situation": "연애"},
    {"text": "어떻게 말을 꺼내야 할지 몰라서 피했어요.", "gender": "남성", "situation": "연애"}
  ]
}
```

---

## 6. GET /emotion/health — 서버 상태 확인

```json
{
  "status": "ok",
  "model": "concat_unweight (hierarchical)"
}
```

---

## Spring이 DB에 저장해야 할 필드 요약

`/ai/round-analyze` 응답의 `updated_*` 필드를 DB에 저장하고, 다음 라운드 요청에 재전달한다.

| DB 컬럼 (권장) | 타입 | 출처 | 설명 |
|----------------|------|------|------|
| `eft_stage` | INTEGER | `updated_eft_stage` | 현재 EFT 단계 (1/2/3) |
| `stage_rounds` | JSONB | `updated_stage_rounds` | 단계별 누적 라운드 수 |
| `stage_progress` | INTEGER | `updated_stage_progress` | 진행도 (0~100) |
| `signals` | JSONB | `updated_signals` | EFT 신호 상태 |
| `user1_message` | JSONB | `updated_f_history` | 여성 대화 히스토리 |
| `user2_message` | JSONB | `updated_m_history` | 남성 대화 히스토리 |
| `cycle_definition` | TEXT | cycle 응답에서 | 사이클 정의 텍스트 |
| `cycle_skip_until` | INTEGER | `cycle_skip_until` | 사이클 거부 시 재시도 라운드 (0이면 즉시) |
| `cycle_agreed_f` | BOOLEAN | Spring 직접 관리 | 여성 사이클 동의 |
| `cycle_agreed_m` | BOOLEAN | Spring 직접 관리 | 남성 사이클 동의 |
| `end_agreed_f` | BOOLEAN | Spring 직접 관리 | 여성 종료 동의 |
| `end_agreed_m` | BOOLEAN | Spring 직접 관리 | 남성 종료 동의 |

---

## 공통 에러 응답 형식

```json
{
  "detail": "에러 메시지"
}
```

| 상태코드 | 의미 |
|---------|------|
| `400` | 잘못된 요청 |
| `422` | Pydantic 유효성 검사 실패 |
| `500` | 서버 내부 오류 |

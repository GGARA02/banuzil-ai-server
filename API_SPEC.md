# 바느질 AI 서버 — API 명세서 (Spring 연동용)

## 기본 정보

| 항목 | 값 |
|------|----|
| Base URL | `http://localhost:8000` (운영 환경에 맞게 변경) |
| Content-Type | `application/json` |
| 인증 | 없음 (내부 서버 간 통신) |
| 인코딩 | UTF-8 |

---

## 상담 세션 전체 흐름

Spring이 이 서버를 호출하는 전체 순서.

```
1. POST /counseling/session       ← 세션 최초 생성 (1회)
        │
        ▼
2. POST /counseling/round         ← 매 라운드마다 반복 호출
        │
        ├─ 응답에 needs_cycle_definition: true
        │         │
        │         ▼
        │  POST /counseling/cycle  ← 사이클 탐색/정의/동의
        │         │ (양측 동의 완료 시 2단계 자동 진입)
        │         │
        ▼         ▼
   (계속 라운드 반복)
        │
        ├─ 3단계 종료 시점 판단
        │         │
        │         ▼
        │  POST /counseling/end    ← 양측 종료 동의 수집
        │
        ▼
3. POST /counseling/report        ← 최종 보고서 생성 (1회)

※ 세션 정리: DELETE /counseling/session/{session_id}
```

### Spring이 처리해야 할 분기

| 응답 필드 | 값 | Spring 처리 |
|-----------|-----|------------|
| `risk_flag` | `true` | 상담 즉시 중단, 전문기관 연계 안내 |
| `needs_cycle_definition` | `true` | `POST /counseling/cycle` 호출 |
| EFT 3단계 도달 판단 | (별도 로직) | `POST /counseling/end` 호출 후 `POST /counseling/report` |

---

## 엔드포인트 목록

| # | Method | Path | 설명 |
|---|--------|------|------|
| 1 | POST | `/counseling/session` | 상담 세션 생성 |
| 2 | POST | `/counseling/round` | 상담 1라운드 실행 |
| 3 | POST | `/counseling/cycle` | 사이클 탐색/정의/동의 |
| 4 | POST | `/counseling/end` | 종료 동의 수집 |
| 5 | POST | `/counseling/report` | 최종 보고서 생성 |
| 6 | DELETE | `/counseling/session/{session_id}` | 세션 삭제 |
| 7 | POST | `/emotion/analyze` | 단일 발화 감성 분석 |
| 8 | POST | `/emotion/analyze/batch` | 일괄 감성 분석 |
| 9 | GET | `/emotion/health` | 서버 상태 확인 |

---

## 1. POST /counseling/session — 세션 생성

상담 시작 전 최초 1회 호출. ECR-R 사전 조사 결과를 전달하면 커플 프로파일을 생성하고 세션을 초기화한다.

### Request Body

| 필드 | 타입 | 필수 | 제약 | 설명 |
|------|------|------|------|------|
| `session_id` | string | ✅ | UUID 권장 | Spring이 생성한 세션 식별자 |
| `f_anxiety` | float | ✅ | 1.0 ~ 7.0 | 여성 ECR-R 불안 점수 |
| `f_avoidance` | float | ✅ | 1.0 ~ 7.0 | 여성 ECR-R 회피 점수 |
| `f_mbti` | string | ❌ | 예: "ENFP" | 여성 MBTI (없으면 빈 문자열) |
| `m_anxiety` | float | ✅ | 1.0 ~ 7.0 | 남성 ECR-R 불안 점수 |
| `m_avoidance` | float | ✅ | 1.0 ~ 7.0 | 남성 ECR-R 회피 점수 |
| `m_mbti` | string | ❌ | 예: "ISTJ" | 남성 MBTI (없으면 빈 문자열) |
| `model_name` | string | ❌ | — | 이 세션에서 사용할 LLM 오버라이드 |
| `bullet_enabled` | boolean | ❌ | — | 총알잡기 ON/OFF 오버라이드 |
| `emotion_weight` | float | ❌ | 0.0 ~ 1.0 | 감성 반영 강도 오버라이드 |

> **ECR-R 점수 안내**: 7점 리커트 척도. 불안 컷오프 2.61 / 회피 컷오프 2.33 기준으로 애착 유형이 분류된다.

### Request 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_anxiety": 4.5,
  "f_avoidance": 2.1,
  "f_mbti": "ENFP",
  "m_anxiety": 1.8,
  "m_avoidance": 4.2,
  "m_mbti": "ISTJ"
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 생성된 세션 ID |
| `status` | string | 항상 `"created"` |
| `classification` | string | 커플 결합 유형 레이블 (예: "불안-거부회피 결합 (전형적 추구-철회 패턴)") |
| `ipv_risk_flag` | boolean | 데이트 폭력 위험군 여부. `true`이면 상담 중 각별한 주의 필요 |
| `message` | string | 안내 메시지 |

### Response 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "created",
  "classification": "불안-거부회피 결합 (전형적 추구-철회 패턴) — 고위험 악순환",
  "ipv_risk_flag": false,
  "message": "세션이 생성되었습니다. 상담을 시작하세요."
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `409 Conflict` | 동일 `session_id`가 이미 존재함 |

---

## 2. POST /counseling/round — 상담 1라운드

매 라운드마다 호출. 여성과 남성의 발화를 함께 보내면 AI 상담 응답을 반환한다. 이 서버가 모든 히스토리와 상태를 관리한다.

### Request Body

| 필드 | 타입 | 필수 | 제약 | 설명 |
|------|------|------|------|------|
| `session_id` | string | ✅ | — | 세션 ID |
| `f_reply` | string | ✅ | 최대 500자 | 여성 내담자 발화 |
| `m_reply` | string | ✅ | 최대 500자 | 남성 내담자 발화 |

### Request 예시

> 여성과 남성은 서로 대화하는 게 아니라, 각자 AI 상담사에게 따로 말을 건다.
> Spring은 각자의 채팅창에서 받은 입력을 묶어서 한 번에 전송한다.

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_reply": "상담사님, 오늘도 파트너한테 연락했는데 한참 뒤에 짧게 답장이 왔어요. 제가 너무 예민한 건지 모르겠는데 자꾸 무시당하는 느낌이에요.",
  "m_reply": "오늘 일이 많아서 답장이 늦었거든요. 근데 파트너가 또 많이 화가 난 것 같아서 어떻게 해야 할지 모르겠어요."
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 ID |
| `f_message` | string | **여성에게 전달할 AI 상담사 메시지** |
| `m_message` | string | **남성에게 전달할 AI 상담사 메시지** |
| `eft_stage` | integer | 현재 EFT 단계 (1 / 2 / 3) |
| `needs_cycle_definition` | boolean | `true`이면 `/counseling/cycle` 호출 필요 |
| `risk_flag` | boolean | `true`이면 위험 신호 감지 — 즉시 상담 중단 |
| `risk_category` | string | 위험 유형 ("자해", "자살", "폭행" 등). risk_flag=true일 때만 유효 |
| `stage_progress` | integer | 현재 단계 진행도 (0~100) |
| `bullet_detected` | boolean | 총알잡기 감지 여부 (로깅용) |
| `eval_score` | float \| null | 응답 품질 가중평균 점수 (1~5). 로깅/모니터링용 |
| `neutrality_result` | object \| null | 중립성 검사 결과 (상세 내용 아래 참고) |

**`neutrality_result` 필드:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `score` | float | 중립성 점수 (1~5) |
| `bias_direction` | string | `"toward_f"` / `"toward_m"` / `"none"` |
| `passed` | boolean | 검사 통과 여부 |
| `violations` | array | 감지된 편향 항목 목록 |
| `regen_triggered` | boolean | 중립성 실패로 재생성이 트리거됐는지 여부 |

### Response 예시 — 정상

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_message": "연락이 없었던 시간 동안 얼마나 불안하고 외로우셨을지... 그 마음이 느껴집니다.",
  "m_message": "바빴던 건 사실이지만, 파트너가 그렇게 느꼈다는 걸 알게 되셨군요.",
  "eft_stage": 1,
  "needs_cycle_definition": false,
  "risk_flag": false,
  "risk_category": "",
  "stage_progress": 35,
  "bullet_detected": false,
  "eval_score": 4.25,
  "neutrality_result": {
    "score": 4.5,
    "bias_direction": "none",
    "passed": true,
    "violations": [],
    "regen_triggered": false
  }
}
```

### Response 예시 — 위험 감지

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_message": "지금 당신의 안전이 가장 중요합니다. 상담을 잠시 중단합니다.",
  "m_message": "지금 당신의 안전이 가장 중요합니다. 상담을 잠시 중단합니다.",
  "eft_stage": 1,
  "risk_flag": true,
  "risk_category": "자해"
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `404 Not Found` | 세션이 없거나 만료됨 (먼저 `/counseling/session` 호출 필요) |
| `500 Internal Server Error` | AI 처리 오류 |

---

## 3. POST /counseling/cycle — 사이클 탐색/정의/동의

1단계에서 `needs_cycle_definition: true` 응답을 받은 후 호출. 상태에 따라 다른 응답을 반환한다.

**호출 시나리오:**
1. 탐색 질문 → 양측 발화 → 사이클 정의 → 양측 동의 순으로 진행
2. 양측 모두 동의(`f_agreed: true`, `m_agreed: true`)하면 2단계로 자동 진입

### Request Body

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | ✅ | 세션 ID |
| `f_agreed` | boolean | ✅ | 여성의 사이클 정의 동의 여부 |
| `m_agreed` | boolean | ✅ | 남성의 사이클 정의 동의 여부 |

### 응답 케이스 3가지

**케이스 A: 탐색 질문 단계** (사이클 정의가 아직 없을 때)

```json
{
  "session_id": "550e8400-...",
  "f_question": "지금까지 나눈 이야기에서, 두 분 사이에 반복되는 패턴이 있다고 느끼시나요?",
  "m_question": "파트너가 연락을 요구할 때, 그 순간 내면에서 어떤 감정이 드셨나요?",
  "cycle_round": 1
}
```

**케이스 B: 사이클 정의 단계** (탐색 후 동의 전)

```json
{
  "session_id": "550e8400-...",
  "cycle_definition": "여성이 연락을 요구하며 다가갈수록 남성은 압도감을 느껴 물러나고, 남성이 물러날수록 여성은 버림받는 공포에 더 강하게 다가가는 '추구-철회' 사이클이 반복되고 있습니다.",
  "message": "양측 동의를 기다립니다."
}
```

**케이스 C: 동의 완료** (`f_agreed: true`, `m_agreed: true`)

```json
{
  "session_id": "550e8400-...",
  "status": "cycle_agreed",
  "next_stage": 2,
  "message": "사이클 동의 완료. 2단계로 진입합니다."
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `404 Not Found` | 세션 없음 |

---

## 4. POST /counseling/end — 종료 동의

3단계 상담 종료 시 양측의 동의를 수집한다. 양측 모두 동의하면 `/counseling/report` 호출 가능.

### Request Body

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | ✅ | 세션 ID |
| `f_agreed` | boolean | ✅ | 여성 종료 동의 여부 |
| `m_agreed` | boolean | ✅ | 남성 종료 동의 여부 |

### Request 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_agreed": true,
  "m_agreed": true
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 ID |
| `both_agreed` | boolean | 양측 모두 동의했는지 여부 |
| `message` | string | 안내 메시지 |

### Response 예시 — 양측 동의

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "both_agreed": true,
  "message": "양측 동의 완료. /counseling/report를 호출하세요."
}
```

### Response 예시 — 미완료

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "both_agreed": false,
  "message": "한쪽이 아직 동의하지 않았습니다."
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `404 Not Found` | 세션 없음 |

---

## 5. POST /counseling/report — 최종 보고서

양측 종료 동의 후 호출. 전체 상담 히스토리를 바탕으로 여성/남성 각각의 맞춤 보고서를 생성한다. 생성에 수십 초가 소요될 수 있다.

### Request Body

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | string | ✅ | 세션 ID |

### Request 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | string | 세션 ID |
| `f_report` | string | 여성 내담자 전용 보고서 (마크다운 또는 평문) |
| `m_report` | string | 남성 내담자 전용 보고서 |

### Response 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "f_report": "## 상담 요약 — 여성 내담자\n\n이번 상담에서 당신은...",
  "m_report": "## 상담 요약 — 남성 내담자\n\n이번 상담에서 당신은..."
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `404 Not Found` | 세션 없음 |
| `500 Internal Server Error` | 보고서 생성 오류 |

---

## 6. DELETE /counseling/session/{session_id} — 세션 삭제

상담 완전 종료 후 세션을 명시적으로 삭제한다. (세션은 24시간 후 자동 만료되기도 함)

### Path Parameter

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `session_id` | string | 삭제할 세션 ID |

### Request 예시

```
DELETE /counseling/session/550e8400-e29b-41d4-a716-446655440000
```

### Response 예시

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "deleted"
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `404 Not Found` | 세션 없음 또는 이미 만료됨 |

---

## 7. POST /emotion/analyze — 단일 감성 분석

상담과 독립적으로 단일 발화의 감성을 분석한다.

### Request Body

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|------|--------|------|
| `text` | string | ✅ | — | 분석할 발화 문장 |
| `gender` | string | ❌ | `"미상"` | 성별 (`"여성"` / `"남성"` / `"미상"`) |
| `situation` | string | ❌ | `"연애"` | 상황 키워드 (`"연애"` / `"결혼"` / `"출산"` 등) |

### Request 예시

```json
{
  "text": "상담사님한테 솔직하게 말하면, 파트너가 연락을 잘 안 해줄 때 저 버려지는 느낌이 들어요.",
  "gender": "여성",
  "situation": "연애"
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `input` | object | 입력값 그대로 반환 |
| `models` | object | 사용된 모델명 |
| `category` | array | 감성 대분류 Top-2. 각 항목: `{rank, label, score}` |
| `detail` | array | 감성 소분류 Top-3. 각 항목: `{rank, label, score}` |

### Response 예시

```json
{
  "input": {
    "gender": "여성",
    "situation": "연애",
    "text": "왜 연락을 안 해? 화가 난다."
  },
  "models": {
    "category_model": "unweighted",
    "detail_model": "low_weight"
  },
  "category": [
    {"rank": 1, "label": "분노", "score": 0.7134},
    {"rank": 2, "label": "상처", "score": 0.1823}
  ],
  "detail": [
    {"rank": 1, "label": "노여워하는", "score": 0.4231},
    {"rank": 2, "label": "짜증내는",   "score": 0.2814},
    {"rank": 3, "label": "억울한",     "score": 0.1502}
  ]
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `400 Bad Request` | `text`가 비어있음 |

---

## 8. POST /emotion/analyze/batch — 일괄 감성 분석

여러 발화를 한 번에 분석한다. 커플 양측 발화를 동시에 처리할 때 사용.

### Request Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `items` | array | `EmotionRequest` 객체 배열 (각 항목은 위 `/emotion/analyze`와 동일) |

### Request 예시

```json
{
  "items": [
    {"text": "상담사님, 파트너가 연락을 잘 안 해줄 때 저 버려지는 느낌이 들어서 너무 힘들어요.", "gender": "여성", "situation": "연애"},
    {"text": "사실 연락을 못 한 게 아니라 어떻게 말을 꺼내야 할지 몰라서 피했던 것 같아요.", "gender": "남성", "situation": "연애"}
  ]
}
```

### Response Body

| 필드 | 타입 | 설명 |
|------|------|------|
| `results` | array | 각 항목의 분석 결과 배열 (순서 보장) |

### Response 예시

```json
{
  "results": [
    {
      "input": {"gender": "여성", "situation": "연애", "text": "상담사님, 파트너가 연락을 잘 안 해줄 때 저 버려지는 느낌이 들어서 너무 힘들어요."},
      "models": {"category_model": "unweighted", "detail_model": "low_weight"},
      "category": [{"rank": 1, "label": "상처", "score": 0.62}, ...],
      "detail":   [{"rank": 1, "label": "외로운", "score": 0.45}, ...]
    },
    {
      "input": {"gender": "남성", "situation": "연애", "text": "사실 연락을 못 한 게 아니라 어떻게 말을 꺼내야 할지 몰라서 피했던 것 같아요."},
      "models": {"category_model": "unweighted", "detail_model": "low_weight"},
      "category": [{"rank": 1, "label": "불안", "score": 0.51}, ...],
      "detail":   [{"rank": 1, "label": "두려운", "score": 0.38}, ...]
    }
  ]
}
```

### 에러

| 상태코드 | 조건 |
|---------|------|
| `400 Bad Request` | `items`가 비어있음 |

---

## 9. GET /emotion/health — 서버 상태 확인

서버 및 감성 모델 로딩 상태를 확인한다.

### Request

파라미터 없음.

### Response 예시

```json
{
  "status": "ok",
  "models": {
    "category_model": "unweighted",
    "detail_model": "low_weight"
  }
}
```

---

## 공통 에러 응답 형식

FastAPI 기본 에러 형식:

```json
{
  "detail": "에러 메시지"
}
```

| 상태코드 | 의미 |
|---------|------|
| `400` | 잘못된 요청 (필수 필드 누락, 값 범위 초과 등) |
| `404` | 세션 없음 또는 만료 |
| `409` | 충돌 (이미 존재하는 세션 ID) |
| `422` | Pydantic 유효성 검사 실패 (타입 오류 등) |
| `500` | 서버 내부 오류 (AI 호출 실패 등) |

---

## 세션 TTL

- 세션은 마지막 호출 시점부터 **24시간** 후 자동 만료된다.
- 만료된 세션에 접근하면 `404` 반환.
- 서버 재시작 시 모든 세션 초기화.

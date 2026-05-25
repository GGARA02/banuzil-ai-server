# AI 서버 API 명세서 — Spring 연동용

> 작성일: 2026-05-17
> AI 서버 주소: `http://banuzil-ai.duckdns.org` (EC2)
> 모든 요청/응답은 `Content-Type: application/json`

---

## 엔드포인트 목록

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/ai/round-analyze` | 라운드 분석 (핵심) |
| POST | `/ai/cycle` | 사이클 탐색/정의 |
| POST | `/ai/report` | 최종 보고서 생성 |

---

## 1. POST /ai/round-analyze — 라운드 분석

매 라운드 양측 발화를 보내면 AI 상담사 응답을 반환한다.
AI 서버가 DB에서 세션 상태·히스토리를 직접 조회하므로, Spring은 최소 정보만 전달.

### Request

```json
{
  "session_id": 1,
  "f_reply": "왜 연락을 안 해줘? 나 무시하는 거야?",
  "m_reply": "바빴어. 왜 그렇게 예민하게 굴어."
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | int | O | mediation_sessions.session_id |
| `f_reply` | string | O | 여성 발화 (max 500자) |
| `m_reply` | string | O | 남성 발화 (max 500자) |

> `bullet_enabled`, `max_refine`은 테스트 전용 옵셔널 필드. Spring은 보내지 않으면 됨.

### Response (200 OK)

```json
{
  "session_id": 1,
  "f_message": "무시받는다고 느끼셨군요. 그 마음이 정말 힘드셨겠어요...",
  "m_message": "바쁜 와중에도 연락이 중요하다는 걸 느끼시는 것 같아요...",
  "needs_cycle_definition": false,
  "risk_flag": false,
  "eft_stage": 1,
  "stage_progress": 0
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `session_id` | int | 요청한 세션 ID |
| `f_message` | string | 여성에게 보낼 AI 상담사 메시지 |
| `m_message` | string | 남성에게 보낼 AI 상담사 메시지 |
| `needs_cycle_definition` | bool | `true`면 사이클 정의 절차 진행 필요 |
| `risk_flag` | bool | `true`면 위험 신호 감지 (자해·자살·폭행 등) |
| `eft_stage` | int | 현재 EFT 단계 (1/2/3). DB에도 저장됨 (참고·UI 표시용) |
| `stage_progress` | int | 현재 단계 진행도 0~100. 3단계에서 90 도달 시 종료 권장 |

> `eft_stage`·`stage_progress`는 AI 서버가 DB(`mediation_sessions`)에도 직접 갱신하므로,
> Spring은 응답에서 받거나 DB에서 읽어도 동일하다. (응답 필드는 참고/표시용)

### Spring 처리 흐름

```
1. 양측 발화 수집 완료
2. mediation_records INSERT (여성 content, 남성 content)
3. POST /ai/round-analyze 호출
4. 응답의 f_message, m_message를 각 사용자에게 전달
5. needs_cycle_definition == true → 사이클 절차 진행
6. risk_flag == true → 위험 알림 처리 (별도 정책에 따라)
```

### 에러 응답

| 상태코드 | 상황 |
|----------|------|
| 404 | 세션을 찾을 수 없음 (`{"detail": "세션을 찾을 수 없습니다: {id}"}`) |
| 500 | AI 처리 오류 (`{"detail": "상담 AI 오류: ..."}`) |

---

## 2. POST /ai/cycle — 사이클 탐색/정의

EFT 1단계에서 부정적 상호작용 사이클을 정의하는 절차.
두 단계로 나뉜다: **탐색 질문 생성** → **사이클 정의 생성**.

### 2-1. 탐색 질문 생성 (답변 없이 호출)

#### Request

```json
{
  "session_id": 1
}
```

#### Response (200 OK) — CycleExploreResponse

```json
{
  "session_id": 1,
  "f_question": "연락이 없을 때 어떤 감정이 가장 먼저 올라오나요?",
  "m_question": "그녀가 연락을 자주 원할 때 어떤 기분이 드시나요?"
}
```

### 2-2. 사이클 정의 생성 (답변 포함 호출)

#### Request

```json
{
  "session_id": 1,
  "f_explore_answer": "불안하고 버림받는 것 같아요",
  "m_explore_answer": "부담스럽고 숨고 싶어요"
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `session_id` | int | O | |
| `f_explore_answer` | string | △ | 여성 탐색 답변 (빈 문자열이면 탐색 모드) |
| `m_explore_answer` | string | △ | 남성 탐색 답변 |

#### Response (200 OK) — CycleDefinitionResponse

```json
{
  "session_id": 1,
  "cycle_definition": "여성이 불안해서 확인을 추구하면 → 남성이 압도되어 철수 → 여성이 더 불안해지는 악순환",
  "f_message": "여성 내담자에게 전달할 상담사 브릿지 메시지 (사이클을 비춰주고 2단계로 초대)",
  "m_message": "남성 내담자에게 전달할 상담사 브릿지 메시지"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `cycle_definition` | string | 사이클 정의 본문 (AI 서버가 `mediation_sessions.cycle_definition`에 저장) |
| `f_message` | string | 사이클 정의 직후 여성에게 줄 상담사 메시지. 빈 문자열일 수 있음(생성 실패 시) |
| `m_message` | string | 남성에게 줄 상담사 메시지 |

> **`f_message`/`m_message`는 사이클 정의 직후의 상담사 응답이다.** 정의문만 보여주면 사용자가
> 무엇에 응답할지 알 수 없으므로, AI가 사이클을 따뜻하게 비춰주고 2단계(더 깊은 마음 표현)로
> 잇는 메시지를 함께 반환한다.

#### Spring이 해야 할 일 (브릿지 메시지 영속화)

`f_message`/`m_message`는 **유저 발화 없이 AI가 단독으로 낸 메시지**다. 다음 라운드 히스토리에
이어지도록 하려면 Spring이 `mediation_records`에 INSERT한다 (기존 계약: Spring INSERT → AI가
ai_response 채움. 단, 여기서는 ai_response까지 Spring이 채워 넣음):

```
INSERT mediation_records (session_id, user_id=f_user_id, content=NULL/'',
                          ai_response = f_message, round_number = 직전 완료 라운드)
INSERT mediation_records (session_id, user_id=m_user_id, content=NULL/'',
                          ai_response = m_message, round_number = 직전 완료 라운드)
```

- **`round_number`는 직전 완료 라운드**(= 현재 `current_round` − 1)로 넣는다.
  다음 라운드 round-analyze의 ai_response UPDATE 필터(`round_number = 다음 라운드`)와 충돌하지 않게.
- **`content`는 빈값/NULL.** 대화창을 `mediation_records` 재조회로 그릴 경우,
  **content가 빈 행은 유저 말풍선을 그리지 말고 ai_response만 AI 말풍선으로 렌더**한다.
- AI 서버의 히스토리 재조립은 `content` 있을 때만 user턴, `ai_response` 있을 때만 assistant턴을
  추가하므로, 이 INSERT만으로 브릿지가 다음 라운드 프롬프트 히스토리에 자연 포함된다.
- 영속화를 생략하면(표시만 하면) UX엔 문제없지만 다음 라운드 AI가 이 메시지를 기억하지 못한다.

### Spring 처리 흐름

```
1. round-analyze 응답에서 needs_cycle_definition == true
2. POST /ai/cycle (답변 없이) → 탐색 질문을 양측에 전달
3. 양측 답변 수집
4. POST /ai/cycle (답변 포함) → 사이클 정의 + 브릿지 메시지(f_message/m_message) 생성
   (AI 서버가 cycle_definition을 DB에 저장)
5. cycle_definition과 f_message/m_message를 양측에 보여줌
   + f_message/m_message를 mediation_records에 INSERT (위 "브릿지 메시지 영속화" 참고)
6. → 다음 라운드의 round-analyze에서 AI 서버가 자동으로 2단계로 전환
   (cycle_definition이 저장되어 있고 1단계 최소 라운드를 채우면 코드가 eft_stage=2로 올림)
```

> **단계 전환은 전적으로 AI 서버가 관리한다.** Spring은 eft_stage를 직접 쓰지 않는다.
> - 1→2: 사이클 정의 저장 후 다음 라운드에 AI가 자동 전환
> - 2→3: 양측 정서 신호 누적(또는 누적 라운드 보조 게이트)으로 AI가 자동 전환
> - 종료: AI가 3단계 progress를 점증시켜 90 도달 → Spring은 `eft_stage==3 && stage_progress>=90`일 때 `/ai/report` 호출 + 세션 완료 처리

---

## 3. POST /ai/report — 최종 보고서 생성

상담 종료 후 호출. AI 서버가 전체 히스토리를 바탕으로 보고서를 생성하고 DB에도 저장한다.

### Request

```json
{
  "session_id": 1
}
```

### Response (200 OK)

```json
{
  "session_id": 1,
  "f_report": {
    "emotion_summary": "상담을 통해 드러난 감정 정리...",
    "partner_understanding": "파트너의 행동 이면에 있는 감정...",
    "mediation_plans": "1. 매일 5분 체크인 대화\n2. ...",
    "recommended_dialogues": "1. '나는 네가 연락이 없으면 불안해져' ..."
  },
  "m_report": {
    "emotion_summary": "...",
    "partner_understanding": "...",
    "mediation_plans": "...",
    "recommended_dialogues": "..."
  }
}
```

### 보고서 섹션 설명

| 섹션 | 설명 |
|------|------|
| `emotion_summary` | 나의 생각과 감정 정리 |
| `partner_understanding` | 파트너 이해 |
| `mediation_plans` | 중재안 (3개 구체적 행동 변화 제안) |
| `recommended_dialogues` | 추천 대화법 (중재안별 실제 대화 예시) |

### Spring 처리 흐름

```
1. 상담 종료 조건 충족 (또는 사용자 요청)
2. POST /ai/report 호출
3. 응답의 f_report / m_report를 각 사용자에게 전달
4. DB에도 mediation_reports 테이블에 자동 저장됨 (AI 서버가 INSERT)
5. 나중에 보고서 재조회 시 mediation_reports 테이블에서 SELECT
```

---

## 공통 사항

### 호출 타이밍

| 엔드포인트 | 언제 호출? |
|------------|-----------|
| `/ai/round-analyze` | 매 라운드 양측 발화 수집 완료 후 |
| `/ai/cycle` | `needs_cycle_definition == true` 일 때 |
| `/ai/report` | 상담 종료 시 |

### AI 서버가 DB에 직접 하는 것들 (Spring이 안 해도 되는 것)

- `mediation_records.ai_response` UPDATE
- `mediation_records.needs_cycle_definition` UPDATE (해당 라운드 f/m 두 행에 동일 값. Spring은 읽기만)
- `mediation_sessions` EFT 상태 UPDATE (eft_stage, stage_progress 등)
- `mediation_sessions.cycle_definition` UPDATE
- `mediation_reports` INSERT

### Spring이 해야 하는 것

- `mediation_records` INSERT (사용자 발화 content)
- AI 서버 API 호출 및 응답 전달
- 프론트엔드에 AI 메시지 / 보고서 전달
- 위험 감지 시 별도 처리 (risk_flag)

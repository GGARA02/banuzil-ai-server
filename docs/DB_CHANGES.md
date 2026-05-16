# DB 변경 사항 — 작업1: DB & 통신 리팩토링

> 작성일: 2026-05-17
> 대상: Supabase (PostgreSQL)
> 실행 위치: Supabase 대시보드 → SQL Editor

---

## 요약

| 테이블 | 변경 | 설명 |
|--------|------|------|
| `mediation_sessions` | ALTER — 6개 컬럼 추가 | EFT 상담 상태를 AI 서버가 직접 관리 |
| `mediation_records` | ALTER — 1개 컬럼 추가 | AI 응답을 사용자 발화와 같은 row에 저장 |
| `mediation_reports` | CREATE — 신규 테이블 | 최종 보고서 4개 섹션 분리 저장 |
| `users` | 변경 없음 | |
| `user_attachments` | 변경 없음 | |
| `friendships` | 변경 없음 | |

---

## 1. mediation_sessions — EFT 상태 컬럼 추가

AI 서버가 매 라운드 처리 후 EFT 상담 상태를 직접 DB에 저장한다.
기존에는 Spring이 요청/응답으로 주고받으며 관리했던 것을 DB-centric으로 전환.

### SQL

```sql
ALTER TABLE mediation_sessions
  ADD COLUMN eft_stage         smallint DEFAULT 1,
  ADD COLUMN stage_rounds      jsonb    DEFAULT '{"1":0,"2":0,"3":0}',
  ADD COLUMN stage_progress    int      DEFAULT 0,
  ADD COLUMN detected_signals  jsonb    DEFAULT '{"f":{},"m":{}}',
  ADD COLUMN cycle_definition  text     DEFAULT '',
  ADD COLUMN cycle_skip_until  int      DEFAULT 0;
```

### 컬럼 설명

| 컬럼 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `eft_stage` | smallint | 1 | 현재 EFT 단계 (1/2/3). 단방향 진행 (후퇴 없음) |
| `stage_rounds` | jsonb | `{"1":0,"2":0,"3":0}` | 단계별 누적 라운드 수 |
| `stage_progress` | int | 0 | 현재 단계 진행도 (0~100) |
| `detected_signals` | jsonb | `{"f":{},"m":{}}` | EFT 진행 신호. 한 번 true가 된 신호는 false로 돌아가지 않음 |
| `cycle_definition` | text | `''` | 부정적 상호작용 사이클 정의 텍스트. 빈 문자열이면 아직 미정의 |
| `cycle_skip_until` | int | 0 | 사이클 거부 시 재시도 기준 라운드. 0이면 즉시 가능 |

### Spring 참고사항

- 이 컬럼들은 **AI 서버가 직접 읽고 씀** — Spring이 관리할 필요 없음
- Spring이 읽어야 하는 경우: `eft_stage` (UI에 단계 표시), `cycle_definition` (사이클 동의 UI)
- `cycle_definition`이 비어있으면 아직 사이클 미정의 상태

---

## 2. mediation_records — AI 응답 컬럼 추가

기존에는 사용자 발화(`content`)만 저장되었으나, AI 상담사 응답도 같은 row에 저장한다.

### SQL

```sql
ALTER TABLE mediation_records
  ADD COLUMN ai_response text;
```

### 변경 전후

```
변경 전:
record_id | session_id | user_id | content        | round_number
1         | 1          | 4       | '서운했어요'     | 1

변경 후:
record_id | session_id | user_id | content        | ai_response      | round_number
1         | 1          | 4       | '서운했어요'     | 'AI 상담 응답...' | 1
```

### 저장 흐름

```
① Spring: mediation_records INSERT (content='사용자 발화', ai_response=NULL)
② Spring: AI 서버에 session_id + f_reply + m_reply 전달
③ AI 서버: 상담 처리 후 해당 라운드 records의 ai_response UPDATE
```

### Spring 참고사항

- Spring의 기존 INSERT 로직은 **변경 불필요** — content만 넣으면 됨
- `ai_response`는 AI 서버가 UPDATE로 채워넣음
- nullable — AI 처리 전에는 NULL 상태

---

## 3. mediation_reports — 신규 테이블

상담 종료 후 최종 보고서를 4개 섹션으로 분리 저장한다.
한 세션당 2개 row (여성 보고서 + 남성 보고서).

### SQL

```sql
CREATE TABLE mediation_reports (
  report_id             serial        PRIMARY KEY,
  session_id            int           NOT NULL REFERENCES mediation_sessions(session_id),
  user_id               int           NOT NULL REFERENCES users(user_id),
  emotion_summary       text          NOT NULL,
  partner_understanding text          NOT NULL,
  mediation_plans       text          NOT NULL,
  recommended_dialogues text          NOT NULL,
  created_at            timestamptz   DEFAULT now(),
  updated_at            timestamptz   DEFAULT now()
);
```

### 컬럼 설명

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `report_id` | serial PK | |
| `session_id` | int FK→mediation_sessions | 세션 참조 |
| `user_id` | int FK→users | 누구의 보고서인지 |
| `emotion_summary` | text | 나의 생각과 감정 정리 |
| `partner_understanding` | text | 파트너 이해 |
| `mediation_plans` | text | 중재안 (3개 행동 변화 제안) |
| `recommended_dialogues` | text | 추천 대화법 (중재안별 대화 예시) |
| `created_at` | timestamptz | 생성 시각 |
| `updated_at` | timestamptz | 수정 시각 |

### Spring 참고사항

- AI 서버가 보고서 생성 후 직접 INSERT
- Spring은 `session_id`와 `user_id`로 조회하여 프론트에 전달
- 섹션별로 분리되어 있으므로 카드/탭 등으로 UI 구성 가능

---

## 변경 후 전체 스키마

### mediation_sessions

```
session_id(PK), initiator_id(FK), participant_id(FK), current_round, status,
eft_stage, stage_rounds, stage_progress, detected_signals,     ← 신규
cycle_definition, cycle_skip_until,                            ← 신규
created_at, updated_at
```

### mediation_records

```
record_id(PK), session_id(FK), user_id(FK), content, ai_response,  ← ai_response 신규
round_number, created_at, updated_at
```

### mediation_reports (신규)

```
report_id(PK), session_id(FK), user_id(FK),
emotion_summary, partner_understanding, mediation_plans, recommended_dialogues,
created_at, updated_at
```

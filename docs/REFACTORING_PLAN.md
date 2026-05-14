# 리팩토링 계획 — session_id 기반 통신

> 작성일: 2026-05-14  
> 목표: Spring이 매 요청마다 전체 컨텍스트를 전달하는 Stateless 구조에서,  
> AI 서버가 Supabase에서 직접 데이터를 읽고 쓰는 DB-centric 구조로 전환

---

## 현재 구조 (변경 전)

```
Spring → AI 서버
  session_id
  f_reply, m_reply          (이번 발화)
  f_mbti, m_mbti            (프로필)
  f_anxiety, f_avoidance    (ECR-R)
  m_anxiety, m_avoidance
  f_history, m_history      (전체 히스토리)
  eft_stage, stage_rounds   (EFT 상태)
  stage_progress, signals
  cycle_definition ...

AI 서버 → Spring
  f_message, m_message
  updated_eft_stage
  updated_stage_rounds
  updated_stage_progress
  updated_signals
  updated_f_history         ← Spring이 저장 후 다음 요청에 재전달
  updated_m_history ...
```

**문제:** 요청/응답 페이로드가 라운드가 쌓일수록 무거워짐.  
Spring이 DB 저장 책임까지 가져야 해서 역할이 과중함.

---

## 목표 구조 (변경 후)

```
Spring → AI 서버
  session_id
  f_reply, m_reply
  bullet_enabled

AI 서버 → Spring
  f_message, m_message
  needs_cycle_definition
  risk_flag, risk_category, risk_keywords
  bullet_detected, bullet_type
```

**라운드 처리 흐름:**
```
① Spring → AI: session_id + f_reply + m_reply
② AI → Supabase: session_id로 EFT 상태 + 히스토리 + 프로필 조회
③ AI: 그래프 실행
④ AI → Supabase: 결과 저장 (EFT 상태 UPDATE + AI 응답 INSERT)
⑤ AI → Spring: f_message + m_message + 플래그
```

---

## DB 변경

### `mediation_sessions` — EFT 상태 컬럼 추가

```sql
ALTER TABLE mediation_sessions
  ADD COLUMN eft_stage        smallint DEFAULT 1,
  ADD COLUMN stage_rounds     jsonb    DEFAULT '{"1":0,"2":0,"3":0}',
  ADD COLUMN stage_progress   int      DEFAULT 0,
  ADD COLUMN detected_signals jsonb    DEFAULT '{"f":{},"m":{}}',
  ADD COLUMN cycle_definition text     DEFAULT '',
  ADD COLUMN needs_cycle_def  boolean  DEFAULT false;
```

### `mediation_records` — 대화 구분 컬럼 추가

```sql
ALTER TABLE mediation_records
  ADD COLUMN role    text DEFAULT 'user',   -- 'user' | 'assistant'
  ADD COLUMN speaker text DEFAULT '';        -- 'f' | 'm'
```

- Spring 발화 저장 시: `role='user'`, `speaker='f'` 또는 `'m'`, `user_id=실제유저`
- AI 응답 저장 시: `role='assistant'`, `speaker='f'` 또는 `'m'`, `user_id=null`

**히스토리 재조립 쿼리:**
```sql
SELECT speaker, role, content
FROM mediation_records
WHERE session_id = 'xxx'
ORDER BY created_at ASC
```
→ `speaker='f'`만 모으면 `f_history`, `speaker='m'`만 모으면 `m_history`

---

## 코드 변경

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `schemas/request.py` | 프로필/히스토리/EFT 상태 삭제 → session_id + f_reply + m_reply + bullet_enabled만 |
| `schemas/response.py` | updated_* 전부 삭제 → f_message + m_message + 플래그만 |
| `services/session_service.py` | **신규** — DB 읽기/쓰기 담당 |
| `routers/counseling.py` | fetch → 그래프 실행 → save 구조로 변경 |

### 무변경 파일

| 파일 | 이유 |
|---|---|
| `graphs/eft_graph.py` | 내부 로직 동일, 입력 형태 그대로 유지 |
| `services/supabase_client.py` | 기존 CRUD 메서드 그대로 사용 |

---

## 신규 파일: `services/session_service.py`

```python
async def fetch_session_context(session_id: str) -> dict:
    """
    라운드 시작 전 호출.
    조회:
      - mediation_sessions → EFT 상태 (eft_stage, stage_rounds, ...)
      - users (initiator_id, participant_id로 JOIN) → mbti, anxiety, avoidance, gender
      - mediation_records → 과거 히스토리 재조립 (f_history, m_history)
    반환: couple_profile, f_history, m_history, eft_state
    """

async def save_round_result(session_id: str, round_num: int, result: dict) -> None:
    """
    라운드 완료 후 호출.
    저장:
      - mediation_records INSERT: f발화, f AI응답, m발화, m AI응답 (4 rows)
      - mediation_sessions UPDATE: eft_stage, signals, stage_progress 등
    """
```

---

## 디버그 정보 처리

응답에서 제거하고 서버 로그로 전환:

```python
# routers/counseling.py
logger.info(
    f"[{session_id}] R{round_num} "
    f"eval={eval_score} | "
    f"neutrality={neutrality_passed} | "
    f"f_emotion={f_emotion} | "
    f"m_emotion={m_emotion}"
)
```

제거 대상 응답 필드:
- `eval_score`, `eval_scores`
- `neutrality_result`
- `f_emotion`, `m_emotion`

---

## Spring 입장에서 달라지는 것

| | 변경 전 | 변경 후 |
|---|---|---|
| 요청 페이로드 | 프로필 + 히스토리 + EFT 상태 전부 | session_id + 발화 2개 + bullet_enabled |
| 응답 페이로드 | updated_* 전부 | 메시지 2개 + 플래그 |
| DB 저장 책임 | Spring | **AI 서버** |
| Spring 구현 부담 | 높음 | 낮음 |

---

## 진행 순서

1. Supabase SQL Editor에서 DB 컬럼 추가 SQL 실행
2. `services/session_service.py` 신규 작성
3. `schemas/request.py` 축소
4. `schemas/response.py` 축소
5. `routers/counseling.py` 리팩토링
6. 테스트 HTML 수정
7. `docs/API_SPEC.md` 업데이트

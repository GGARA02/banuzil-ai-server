# 리팩토링 계획

> 최종 수정: 2026-05-15  
> 상태: 작업2 완료, 작업3 완료. 작업1 미착수.

---

## 작업 구분

| # | 작업 | 상태 | 설명 |
|---|------|------|------|
| 1 | DB & 통신 리팩토링 | ❌ 미착수 | session_id 기반 DB-centric 구조 전환 |
| 2 | 사이클 흐름 재설계 | ✅ 완료 | 탐색 답변 필드, 사이클 진입 라운드, 거부 후 재시도 |
| 3 | 프롬프트 품질 개선 | ✅ 완료 | 말투 통일, 발화 전달 버그 수정, 감정 단정 금지 |

---

## 작업1: DB & 통신 리팩토링 (미착수)

### 목표

Spring이 매 요청마다 전체 컨텍스트를 전달하는 Stateless 구조에서,  
AI 서버가 Supabase에서 직접 데이터를 읽고 쓰는 DB-centric 구조로 전환.

### 현재 구조 (변경 전)

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
  cycle_definition
  cycle_skip_until ...

AI 서버 → Spring
  f_message, m_message
  updated_eft_stage
  updated_stage_rounds
  updated_stage_progress
  updated_signals
  updated_f_history
  updated_m_history
  needs_cycle_definition
  cycle_skip_until ...
```

**문제:** 요청/응답 페이로드가 라운드가 쌓일수록 무거워짐.  
Spring이 DB 저장 책임까지 가져야 해서 역할이 과중함.

### 목표 구조 (변경 후)

```
Spring → AI 서버
  session_id
  f_reply, m_reply
  bullet_enabled

AI 서버 → Spring
  f_message, m_message
  needs_cycle_definition
  cycle_skip_until
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

### DB 변경

#### `mediation_sessions` — EFT 상태 컬럼 추가

```sql
ALTER TABLE mediation_sessions
  ADD COLUMN eft_stage         smallint DEFAULT 1,
  ADD COLUMN stage_rounds      jsonb    DEFAULT '{"1":0,"2":0,"3":0}',
  ADD COLUMN stage_progress    int      DEFAULT 0,
  ADD COLUMN detected_signals  jsonb    DEFAULT '{"f":{},"m":{}}',
  ADD COLUMN cycle_definition  text     DEFAULT '',
  ADD COLUMN cycle_skip_until  int      DEFAULT 0,
  ADD COLUMN needs_cycle_def   boolean  DEFAULT false;
```

#### `mediation_records` — 대화 구분 컬럼 추가

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

### 코드 변경

| 파일 | 변경 내용 |
|---|---|
| `schemas/request.py` | 프로필/히스토리/EFT 상태 삭제 → session_id + f_reply + m_reply + bullet_enabled만 |
| `schemas/response.py` | updated_* 전부 삭제 → f_message + m_message + 플래그만 |
| `services/session_service.py` | **신규** — DB 읽기/쓰기 담당 |
| `routers/counseling.py` | fetch → 그래프 실행 → save 구조로 변경 |

**무변경 파일:**

| 파일 | 이유 |
|---|---|
| `graphs/eft_graph.py` | 내부 로직 동일, 입력 형태 그대로 유지 |
| `services/supabase_client.py` | 기존 CRUD 메서드 그대로 사용 |

### 신규 파일: `services/session_service.py`

```python
async def fetch_session_context(session_id: str) -> dict:
    """
    라운드 시작 전 호출.
    조회:
      - mediation_sessions → EFT 상태 (eft_stage, stage_rounds, cycle_skip_until, ...)
      - users (initiator_id, participant_id로 JOIN) → mbti, anxiety, avoidance, gender
      - mediation_records → 과거 히스토리 재조립 (f_history, m_history)
    반환: couple_profile, f_history, m_history, eft_state
    """

async def save_round_result(session_id: str, round_num: int, result: dict) -> None:
    """
    라운드 완료 후 호출.
    저장:
      - mediation_records INSERT: f발화, f AI응답, m발화, m AI응답 (4 rows)
      - mediation_sessions UPDATE: eft_stage, signals, stage_progress, cycle_skip_until 등
    """
```

### 디버그 정보 처리

응답에서 제거하고 서버 로그로 전환:

```python
logger.info(
    f"[{session_id}] R{round_num} "
    f"eval={eval_score} | neutrality={neutrality_passed} | "
    f"f_emotion={f_emotion} | m_emotion={m_emotion}"
)
```

### 진행 순서

1. Supabase SQL Editor에서 DB 컬럼 추가 SQL 실행
2. `services/session_service.py` 신규 작성
3. `schemas/request.py` 축소
4. `schemas/response.py` 축소
5. `routers/counseling.py` 리팩토링
6. 테스트 HTML 수정
7. `docs/API_SPEC.md` 업데이트

---

## 작업2: 사이클 흐름 재설계 (완료)

### 변경 내용

#### 사이클 진입 라운드 감지 (`is_cycle_round`)

- `node_eft_stage_router`에서 신호 충족 여부를 **응답 생성 전에** 미리 판단
- `is_cycle_round = True`이면 응답 프롬프트에서 질문 규칙을 제거
- AI가 상대방 감정만 전달하고 질문 없이 따뜻하게 마무리

#### 탐색 답변 필드 추가

- `CycleRequest`에서 `cycle_definition` 제거
- 대신 `f_explore_answer`, `m_explore_answer` 추가
- 답변 없이 호출 → 탐색 질문 생성
- 답변 포함 호출 → 히스토리 + 답변 기반으로 사이클 정의 생성

#### 사이클 거부 후 재시도

- `cycle_skip_until` 필드 추가 (요청/응답/DB 공통)
- 거부 시 Spring이 `cycle_skip_until = round_num + 2` 저장
- AI 서버는 `round_num >= cycle_skip_until`일 때만 사이클 진입 판단

#### 사이클 전체 흐름

```
Round N: 1단계 신호 충족 + round_num >= cycle_skip_until
  → is_cycle_round = true
  → AI 응답: 감정 전달만, 질문 없이 마무리
  ↓
Spring: 사이클 UI 진입
  ↓
POST /ai/cycle (답변 없이)
  → { f_question, m_question }
  ↓
유저 답변 입력
  ↓
POST /ai/cycle (f_explore_answer + m_explore_answer)
  → { cycle_definition }
  ↓
Spring: 정의 표시
  ├─ 동의 → cycle_definition 저장 + stage=2
  └─ 거부 → cycle_skip_until = round_num + 2
            → 2라운드 더 진행 후 재시도
```

### 변경 파일

| 파일 | 변경 |
|---|---|
| `schemas/request.py` | `RoundAnalyzeRequest`에 `cycle_skip_until` 추가. `CycleRequest`에 `f_explore_answer`/`m_explore_answer` 추가 |
| `schemas/response.py` | `RoundAnalyzeResponse`에 `cycle_skip_until` 추가 |
| `config/prompts/eft_base.py` | `build_user_message()`에 `is_cycle_round` 파라미터. `build_cycle_definition_prompt()`에 답변 파라미터 |
| `graphs/eft_graph.py` | `EFTState`에 `cycle_skip_until`, `is_cycle_round`. `node_eft_stage_router`에서 사이클 선행 판단 |
| `routers/counseling.py` | `cycle_skip_until` 요청→state→응답 전달. `/ai/cycle` 분기 변경 |
| `test_client.html` | 답변 입력 UI, 거부 시 `cycleSkipUntil` 세팅 |

---

## 작업3: 프롬프트 품질 개선 (완료)

### 변경 내용

| 항목 | 변경 |
|---|---|
| 말투 | `~요` 체로 통일 |
| 애착유형/MBTI 활용 | 접근 톤·질문 스타일 조정에만 사용, 이론 직접 설명 금지 |
| 감정 단정 금지 | 열린 질문/조심스러운 추론으로만 확인 |
| 상대방 발화 전달 | `state["m_reply"]` 직접 사용 (히스토리가 비어있는 1라운드 버그 수정) |
| 자신의 발화 반영 | `self_reply` 파라미터 추가 (AI가 유저 발화를 무시하던 버그 수정) |
| 공격적 표현 완화 | "있는 그대로 전달" → "마음이나 걱정으로 부드럽게 재구성" |
| 라운드별 캡 | R1-2: Step1, R3: Step2, R4+: Step3 (신호 기반 하한) |
| `ATTACHMENT_PROMPT_WEIGHT` | 애착유형 상세 정보의 프롬프트 반영 강도 (기본 1.0) |
| Self-Refine 토글 | `max_refine=0`이면 self_refine 노드 완전 스킵 |
| 평가 기준 완화 | `EVAL_PASS_SCORE` 4.0 → 3.5 |

### 변경 파일

| 파일 | 변경 |
|---|---|
| `config/prompts/eft_base.py` | 시스템 프롬프트, 유저 메시지 빌더 전면 개선 |
| `config/prompts/stage_prompts.py` | 라운드별 캡, Step4 제거, 신호 지침 개선 |
| `graphs/eft_graph.py` | 파트너 발화 전달 수정, self_reply 추가, max_refine=0 스킵 |
| `config/settings.py` | `ATTACHMENT_PROMPT_WEIGHT` 추가, `EVAL_PASS_SCORE` 기본값 변경 |

---

## Spring 입장에서 달라지는 것 (작업1 완료 후)

| | 변경 전 | 변경 후 |
|---|---|---|
| 요청 페이로드 | 프로필 + 히스토리 + EFT 상태 전부 | session_id + 발화 2개 + bullet_enabled |
| 응답 페이로드 | updated_* 전부 | 메시지 2개 + 플래그 |
| DB 저장 책임 | Spring | **AI 서버** |
| Spring 구현 부담 | 높음 | 낮음 |

# Spring 대응 가이드 — 사이클 처리 전면 정리 (2026-05-25)

> AI 서버 주소: `http://banuzil-ai.duckdns.org`  
> 모든 요청/응답: `Content-Type: application/json`

Spring 작업자에게 전달하는 **단일 가이드 문서**다.  
`/ai/cycle` 전체 흐름(탐색 질문 + 사이클 정의)과 이번 변경사항을 함께 담았다.

---

## 이번에 무엇이 바뀌었나

### 변경 전

`POST /ai/cycle`을 답변과 함께 호출하면 사이클 정의문만 반환했다.

```json
// 기존 CycleDefinitionResponse
{
  "session_id": 1,
  "cycle_definition": "여자친구가 확인을 요청하면 남자친구가 멀어지고..."
}
```

사이클 정의가 끝나면 **내담자에게 줄 상담사 메시지가 없었다.**  
사용자는 정의문만 보고 무엇에 응답해야 할지 알 수 없는 빈 구간이 생겼다.

### 변경 후

이제 사이클 정의와 함께 **상담사 브릿지 메시지**를 양측별로 함께 반환한다.  
브릿지 메시지는 사이클을 따뜻하게 비춰주고 2단계(더 깊은 마음 표현)로 잇는 상담사 발화다.

```json
// 변경된 CycleDefinitionResponse
{
  "session_id": 1,
  "cycle_definition": "여자친구가 확인을 요청하면 남자친구가 멀어지고...",
  "f_message": "두 분이 서로 상처주려 한 게 아니라, 같은 패턴 안에서 함께 힘들어하셨던 것 같아요. 그 안에서 당신이 느꼈던 감정을 좀 더 나눠주실 수 있을까요?",
  "m_message": "두 분 모두 이 고리에서 벗어나고 싶으셨던 것 같아요. 그때 당신 마음속엔 어떤 감정이 있었는지 조금 더 이야기해주실 수 있을까요?"
}
```

**Spring이 추가로 해야 할 일:**
- `CycleDefinitionResponse` DTO에 `f_message` / `m_message` 필드 추가
- 두 메시지를 각 내담자 화면에 상담사 말풍선으로 표시
- 두 메시지를 `mediation_records`에 INSERT (히스토리 연속성 — 아래 상세 설명)

---

## /ai/cycle 전체 흐름

`/ai/cycle`은 같은 엔드포인트를 **두 번** 순서대로 호출하는 절차다.  
`f_explore_answer` / `m_explore_answer` 필드 포함 여부로 모드가 결정된다.

```
needs_cycle_definition == true
        ↓
[1단계] 탐색 질문 받기   → 양측에 질문 표시, 답변 수집
        ↓
[2단계] 사이클 정의 받기 → 정의문 + 브릿지 메시지 표시, records INSERT
        ↓
다음 라운드 진행 (AI 서버가 자동 2단계 전환)
```

---

## 실제 동작 예시 (화면 기준)

아래는 실제 테스트에서 사이클 절차가 진행된 흐름이다.  
각 화면 이벤트가 어떤 API 호출에서 비롯되는지 매핑했다.

```
[라운드 종료 후 round-analyze 응답 수신]

  needs_cycle_definition: true  ← 이 값이 true이면 사이클 절차 시작
  eft_stage: 1
  stage_progress: 75

        Spring 처리:
        → 화면에 "사이클 정의 절차 필요" 안내 표시
        → 즉시 POST /ai/cycle 호출 (답변 없이)
```

```
[POST /ai/cycle 호출 — 답변 없이]

  Request:  { "session_id": 1 }

  Response: {
    "f_question": "당신이 그분에게 연락을 요구했을 때,
                   그분이 멀어지면서 느끼는 초라함이 더 커졌을 때
                   당신은 어떤 생각이나 감정을 하게 되나요?",
    "m_question": "..."
  }

        Spring 처리:
        → f_question을 여성 화면에 상담사 질문으로 표시
        → m_question을 남성 화면에 상담사 질문으로 표시
        → 양측 답변 입력 대기
```

```
[여성 내담자 답변 입력]

  "그때 진짜 내가 되게 하찮아진 느낌 들어. 나 하나 챙기는 것도 귀찮은 사람한테
   내가 매달리고 있나 싶어서, 서운한데도 더 말하면 더 밀어낼까 봐 겁나고."

        Spring 처리:
        → 답변 저장 (f_explore_answer)
        → 남성 답변도 수집 완료되면 다음 단계
```

```
[POST /ai/cycle 호출 — 양측 답변 포함]

  Request: {
    "session_id": 1,
    "f_explore_answer": "그때 진짜 내가 되게 하찮아진 느낌 들어...",
    "m_explore_answer": "..."
  }

  Response: {
    "cycle_definition": "여자친구가 연락이 없자 혼자 매달리는 느낌과 초라함이
                         커지면서 서운함을 느끼게 되고, 남자친구는 바쁜 상황에서
                         자신의 진심이 의심받는 것에 답답함을 느껴 더욱 마음을
                         닫게 됩니다. 이처럼 서로의 감정이 악순환하며 반복되는
                         패턴이 형성됩니다.",
    "f_message": "당신은 답이 없을수록 더 초라해지고, 나만 매달리는 사람 같아져서
                  그분의 마음을 더 확인하고 싶어져요...",
    "m_message": "..."
  }

        Spring 처리:
        → cycle_definition 화면에 표시 (공유 정보)
        → f_message를 여성 화면에 상담사 말풍선으로 표시   ← "AI 상담사 (사이클 정리)" 말풍선
        → m_message를 남성 화면에 상담사 말풍선으로 표시
        → mediation_records에 2행 INSERT (아래 참고)
        → "사이클 절차 완료 — 다음 라운드에 2단계 전환 예상" 안내 표시
```

```
[다음 라운드 진행]

  → 다음 라운드 round-analyze에서 AI가 자동으로 eft_stage=2로 전환
  → Spring은 별도 처리 없음
```

> **핵심 포인트:**  
> 탐색 질문 답변은 mediation_records에 저장하지 않아도 된다. Spring이 임시 보관했다가  
> 두 번째 `/ai/cycle` 호출 시 body에 담아 보내면 된다.  
> AI 서버가 DB에서 직접 읽지 않고 요청 body에서 받는다.

---

### 모드 1 — 탐색 질문 받기

**언제 호출?**  
`POST /ai/round-analyze` 응답에서 `needs_cycle_definition == true`가 오면 즉시 호출.

**Request** — 답변 없이 `session_id`만 전달

```json
{
  "session_id": 1
}
```

**Response — `CycleExploreResponse`**

```json
{
  "session_id": 1,
  "f_question": "연락이 없을 때 어떤 감정이 가장 먼저 올라오나요?",
  "m_question": "그녀가 연락을 자주 원할 때 어떤 기분이 드시나요?"
}
```

**Spring이 할 일**

1. `f_question` → 여성 내담자 화면에 상담사 질문으로 표시
2. `m_question` → 남성 내담자 화면에 상담사 질문으로 표시
3. 양측 답변 입력을 받아 저장 (다음 호출에 사용)

```java
CycleExploreResponse explore = aiServerClient.cycleExplore(sessionId);
// explore.getFQuestion() → 여성 화면에 표시
// explore.getMQuestion() → 남성 화면에 표시
```

---

### 모드 2 — 사이클 정의 + 브릿지 메시지 받기

**언제 호출?**  
탐색 질문에 대한 양측 답변 수집 완료 후 호출.

**Request** — 양측 답변 포함

```json
{
  "session_id": 1,
  "f_explore_answer": "불안하고 버림받는 것 같아요",
  "m_explore_answer": "부담스럽고 숨고 싶어요"
}
```

**Response — `CycleDefinitionResponse`** (이번에 변경된 부분)

```json
{
  "session_id": 1,
  "cycle_definition": "여자친구가 불안해서 확인을 요청하면, 남자친구는 압도되어 멀어지고, 그럴수록 여자친구는 더 불안해지는 패턴이 반복됩니다.",
  "f_message": "두 분이 서로 상처주려 한 게 아니라, 같은 패턴 안에서 함께 힘들어하셨던 것 같아요...",
  "m_message": "두 분 모두 이 고리에서 벗어나고 싶으셨던 것 같아요..."
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `cycle_definition` | string | 사이클 정의문 — AI 서버가 `mediation_sessions.cycle_definition`에도 저장 |
| `f_message` | string | 여성에게 전달할 상담사 브릿지 메시지. **빈 문자열일 수 있음** |
| `m_message` | string | 남성에게 전달할 상담사 브릿지 메시지. **빈 문자열일 수 있음** |

**Spring이 할 일**

1. `cycle_definition` → 양측 화면에 사이클 정의로 표시
2. `f_message` → 여성 화면에 상담사 말풍선으로 표시
3. `m_message` → 남성 화면에 상담사 말풍선으로 표시
4. `f_message` / `m_message` → `mediation_records`에 INSERT (아래 참고)
5. 이후 다음 라운드 진행 — AI 서버가 자동으로 2단계 전환, Spring 별도 처리 없음

---

## 브릿지 메시지 DB INSERT

브릿지 메시지는 유저 발화 없이 AI가 단독으로 낸 메시지다.  
다음 라운드 히스토리에 이어지도록 Spring이 `mediation_records`에 2행 INSERT한다.

```sql
INSERT INTO mediation_records (session_id, user_id, content, ai_response, round_number)
VALUES (:session_id, :f_user_id, NULL, :f_message, :prev_round_number);

INSERT INTO mediation_records (session_id, user_id, content, ai_response, round_number)
VALUES (:session_id, :m_user_id, NULL, :m_message, :prev_round_number);
```

| 컬럼 | 값 |
|------|----|
| `content` | NULL 또는 빈 문자열 (유저 발화 없음) |
| `ai_response` | 받은 `f_message` / `m_message` |
| `round_number` | **직전 완료 라운드** (= 현재 `current_round` − 1) |

**`round_number`를 직전 라운드로 넣는 이유:**  
다음 라운드 `/ai/round-analyze` 처리 시 AI 서버가 `round_number = 다음 라운드`로 `ai_response`를 UPDATE한다.  
브릿지를 다음 라운드 번호에 넣으면 이 UPDATE가 덮어쓴다. 직전 라운드에 넣어 충돌을 피한다.

**빈 메시지 처리:**  
`f_message` 또는 `m_message`가 빈 문자열(`""`)이면 해당 행은 INSERT 생략.

---

## 대화창 렌더링 — content 빈 행 처리

`mediation_records`를 재조회해서 대화 내역을 그리는 경우,  
이제 `content`가 비어 있고 `ai_response`만 있는 행(브릿지 행)이 존재한다.

- `content`가 비어 있으면 **유저 말풍선을 그리지 않고**, `ai_response`만 상담사 말풍선으로 표시
- 사이클이 발생한 라운드에는 records 행이 더 많을 수 있음 (발화 2행 + 브릿지 2행)

---

## Java DTO 수정

```java
// CycleDefinitionResponse.java — f_message / m_message 필드 추가
public class CycleDefinitionResponse {
    @JsonProperty("session_id")
    private Long sessionId;

    @JsonProperty("cycle_definition")
    private String cycleDefinition;

    @JsonProperty("f_message")
    private String fMessage;   // 신규 추가 — 빈 문자열일 수 있음

    @JsonProperty("m_message")
    private String mMessage;   // 신규 추가 — 빈 문자열일 수 있음
}
```

> 기존 Spring이 `f_message` / `m_message`를 무시해도 동작에 지장 없다 (하위 호환).  
> 단, 표시와 INSERT를 하지 않으면 사이클 직후 상담사 메시지가 빈 채로 남는다.

---

## 체크리스트

- [ ] `CycleDefinitionResponse` DTO에 `f_message` / `m_message` 필드 추가
- [ ] 탐색 질문(`f_question` / `m_question`)을 각 내담자 화면에 표시
- [ ] 사이클 정의(`cycle_definition`)를 화면에 표시
- [ ] 브릿지 메시지(`f_message` / `m_message`)를 각 화면에 상담사 말풍선으로 표시
- [ ] 브릿지 메시지를 `mediation_records`에 INSERT (`content=NULL`, `round_number=직전 라운드`)
- [ ] 대화창 재조회 시 `content` 빈 행은 유저 말풍선 생략
- [ ] 빈 문자열 메시지는 표시 · INSERT 생략

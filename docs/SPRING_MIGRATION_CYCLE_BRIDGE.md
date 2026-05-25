# Spring 대응 가이드 — 사이클 브릿지 메시지 (2026-05-25)

> 이번 AI 서버 변경에 맞춰 **Spring이 해야 할 일**만 정리한 문서.
> 상세 API 명세는 [`API_SPEC_FOR_SPRING.md`](API_SPEC_FOR_SPRING.md) 2장 참고.

---

## 변경 요약 (Spring 관점)

| # | 변경 | Spring 대응 |
|---|------|-------------|
| 1 | `POST /ai/cycle` (정의 모드) 응답에 **`f_message` / `m_message` 추가** | **필요** — 표시 + DB INSERT |
| 2 | 1단계 강제 사이클 진입 라운드 `4 → 3`으로 단축 | **불필요** — AI 내부 페이싱, Spring 무관 |
| 3 | 테스트 HTML EFT 신호 시각화 (mock 전용 엔드포인트) | **불필요** — 테스트 도구 전용 |

실제로 Spring 코드 변경이 필요한 것은 **#1 (사이클 브릿지 메시지)** 하나다.

---

## 스키마 변경 정리 — 기존 vs 변경 + 적용 이유

### DB 스키마: **변경 없음**

- 새 테이블·새 컬럼 **없음**. 기존 `mediation_records`의 컬럼(`content`, `ai_response`,
  `round_number`)을 그대로 사용한다. **마이그레이션 불필요.**
- 다만 사용 방식이 하나 늘어난다: 지금까지 `mediation_records`는 "유저 발화 1행"이었는데,
  이제 **유저 발화 없이 `ai_response`만 채운 행(브릿지)** 이 추가될 수 있다.

### API 응답 스키마: `CycleDefinitionResponse` 필드 추가

| 필드 | 기존 | 변경 | 비고 |
|------|------|------|------|
| `session_id` | ✅ | ✅ | 그대로 |
| `cycle_definition` | ✅ | ✅ | 그대로 |
| `f_message` | ✖ | ✅ **추가** | 여성에게 줄 상담사 브릿지 메시지(빈 문자열 가능) |
| `m_message` | ✖ | ✅ **추가** | 남성에게 줄 상담사 브릿지 메시지(빈 문자열 가능) |

```
// 기존
{ "session_id": 1, "cycle_definition": "..." }

// 변경
{ "session_id": 1, "cycle_definition": "...", "f_message": "...", "m_message": "..." }
```

### 적용 이유 (왜 바꾸나)

- 기존: `/ai/cycle` 정의 모드가 사이클 정의문만 반환 → 정의가 끝나면 **내담자에게 줄 상담사
  발화가 없어**, 사용자가 무엇에 응답할지 모르는 빈 구간이 생겼다.
- 변경: 사이클을 따뜻하게 비춰주고 2단계(더 깊은 마음 표현)로 잇는 **상담사 메시지**를
  양측별로 함께 반환 → 사용자가 이어서 답할 대상이 생기고, DB에 남기면 다음 라운드
  히스토리로도 연결된다(상담 연속성).
- DTO만 늘렸으므로 **하위 호환**: 구버전 Spring이 새 필드를 무시해도 동작에 지장 없다.

### 어떻게 수정하나 (요약)

1. 응답 DTO에 `f_message`/`m_message` 필드 추가(아래 예시)
2. 두 메시지를 각 내담자 화면에 상담사 메시지로 표시
3. 두 메시지를 `mediation_records`에 INSERT(`content=NULL`, `ai_response=메시지`,
   `round_number=직전 완료 라운드`)
4. 대화창을 records로 그릴 경우, `content` 빈 행은 유저 말풍선 생략

```java
// 1) 응답 DTO 필드 추가 (예: CycleDefinitionResponse.java)
public class CycleDefinitionResponse {
    private Long   sessionId;
    private String cycleDefinition;
    private String fMessage;   // 추가 (nullable / 빈 문자열 가능)
    private String mMessage;   // 추가
    // getter/setter ...
}
```

상세 수정 절차는 아래 "Spring이 해야 할 일"과 끝의 체크리스트 참고.

---

## #1 사이클 브릿지 메시지 — 무엇이 바뀌었나

기존에 `/ai/cycle` 정의 모드는 `cycle_definition`만 반환했다. 그래서 사이클 정의가 끝나면
사용자에게 보여줄 **상담사 발화가 없어**, 사용자가 무엇에 응답해야 할지 알 수 없었다.

이제 AI 서버가 사이클을 따뜻하게 비춰주고 2단계(더 깊은 마음 표현)로 잇는 **상담사 메시지**를
양측(여성/남성)별로 함께 생성해 반환한다.

### 변경된 응답 (CycleDefinitionResponse)

```json
{
  "session_id": 1,
  "cycle_definition": "여자친구가 ~하면, 남자친구는 ~하고, 그러면 여자친구는 더욱 ~하는 패턴이 반복됩니다.",
  "f_message": "여성 내담자에게 전달할 상담사 메시지 (사이클을 비춰주고 2단계로 초대)",
  "m_message": "남성 내담자에게 전달할 상담사 메시지"
}
```

- `f_message` / `m_message`는 **빈 문자열일 수 있다**(브릿지 생성 실패 시). 빈 값이면 표시·저장을 생략하면 된다.
- `cycle_definition` 자체는 기존과 동일하게 AI 서버가 `mediation_sessions.cycle_definition`에 이미 저장한다.

---

## Spring이 해야 할 일

### (1) 사용자에게 브릿지 메시지 표시

`/ai/cycle` 정의 응답을 받으면, 사이클 정의문과 함께 `f_message`/`m_message`를 각 내담자
화면에 **상담사 메시지(AI 말풍선)** 로 보여준다. 이 메시지가 사용자가 다음 라운드에서
응답할 대상이 된다.

### (2) 브릿지 메시지를 `mediation_records`에 INSERT (히스토리 연속성)

브릿지 메시지는 **유저 발화 없이 AI가 단독으로 낸 메시지**다. 다음 라운드 round-analyze의
히스토리에 이어지도록 하려면 Spring이 records에 2행 INSERT 한다.

```sql
INSERT INTO mediation_records (session_id, user_id, content, ai_response, round_number)
VALUES (:session_id, :f_user_id, NULL, :f_message, :prev_round);

INSERT INTO mediation_records (session_id, user_id, content, ai_response, round_number)
VALUES (:session_id, :m_user_id, NULL, :m_message, :prev_round);
```

규칙:
- **`content` = NULL(또는 빈 문자열).** 유저 발화가 없으므로 비운다.
- **`ai_response` = 받은 `f_message`/`m_message`.**
- **`round_number` = 직전 완료 라운드** = `현재 current_round - 1`.
  - 이유: 다음 라운드(N+1) round-analyze 처리 시 AI 서버가
    `mediation_records ... WHERE round_number = N+1 AND user_id = ?` 로 `ai_response`를
    UPDATE 한다. 브릿지를 N+1에 넣으면 이 UPDATE가 브릿지 행을 덮어쓴다. 그래서
    **직전 완료 라운드(N)** 에 넣어 충돌을 피한다.
- 빈 메시지(`""`)는 INSERT 생략.

> 영속화를 생략하고 표시만 해도 사용자 UX엔 문제없다. 다만 그 경우 **다음 라운드의 AI가
> 자신이 비춰준 사이클 메시지를 기억하지 못한다**(히스토리에서 빠짐). 연속성을 원하면 INSERT 권장.

### (3) 대화창 렌더링 — 빈 content 행 처리

`mediation_records`를 재조회해서 대화 내역을 그리는 경우, 이제 **`content`가 비어 있고
`ai_response`만 있는 행**이 존재한다(위 브릿지 행). 렌더링 시:

- `content`가 비어 있으면 **유저 말풍선을 그리지 말고**, `ai_response`만 상담사 말풍선으로 표시.
- "라운드당 record 2행" 같은 가정을 쓰고 있었다면, 사이클이 일어난 라운드에는 행이 더 많을 수
  있음을 감안한다(해당 라운드 = 발화 2행 + 브릿지 2행).

### AI 서버 측 참고 (Spring이 알아둘 점)

- AI 서버의 히스토리 재조립은 `content`가 있을 때만 user턴, `ai_response`가 있을 때만
  assistant턴을 추가한다. 따라서 위 INSERT만으로 브릿지가 다음 라운드 프롬프트에 자연 포함되며,
  **AI 서버에 추가 작업은 없다.**

---

## #2 사이클 강제 진입 라운드 단축 (참고, 대응 불필요)

1단계에서 양측 EFT 신호가 충분히 안 잡혀도, 누적 라운드가 일정 수 이상이면 사이클 절차로
강제 진입한다(철회형 커플이 1단계에 무한 정체하는 것 방지). 이 기준이 **4라운드 → 3라운드**로
짧아졌다.

- Spring 대응: **없음.** 단계 전환은 전적으로 AI 서버가 관리한다.
- 체감 변화: `needs_cycle_definition == true`가 **한 라운드 더 일찍** 올 수 있다. 사이클 절차
  UI를 띄우는 기존 트리거(`needs_cycle_definition`)는 그대로 동작한다.

---

## 체크리스트

- [ ] `/ai/cycle` 정의 응답에서 `f_message`/`m_message` 파싱 (DTO 필드 추가)
- [ ] 두 메시지를 각 내담자 화면에 상담사 메시지로 표시
- [ ] 두 메시지를 `mediation_records`에 INSERT (content=NULL, ai_response=메시지, round_number=직전 완료 라운드)
- [ ] 대화창 재조회 렌더링 시 content 빈 행은 유저 말풍선 생략
- [ ] (확인) 빈 문자열 메시지는 표시·저장 생략 처리

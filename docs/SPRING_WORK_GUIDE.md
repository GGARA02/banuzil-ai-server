# Spring 작업 가이드 — AI 서버 연동

> 작성일: 2026-05-17
> 참고 문서: `DB_CHANGES.md` (DB 변경), `API_SPEC_FOR_SPRING.md` (API 명세)

---

## 현재 Spring 상태

- `MediationService.submitRecord()`: 발화 저장 + 3라운드 고정 종료
- AI 서버 호출 없음 (Spring AI 의존성만 있고 미사용)
- `MediationRecord` 엔티티에 `ai_response` 컬럼 없음
- `MediationReport` 엔티티 없음
- `MediationSession` 엔티티에 EFT 상태 컬럼 없음

---

## 작업 목록

### 1. DB 마이그레이션 반영 (엔티티 수정)

> SQL은 `DB_CHANGES.md` 참조. Supabase SQL Editor에서 실행.

#### 1-1. MediationSession 엔티티 — 컬럼 추가

```java
// 기존 필드에 추가
private Integer eftStage = 1;           // smallint DEFAULT 1

@Column(columnDefinition = "jsonb")
private String stageRounds;             // jsonb DEFAULT '{"1":0,"2":0,"3":0}'

private Integer stageProgress = 0;      // int DEFAULT 0

@Column(columnDefinition = "jsonb")
private String detectedSignals;         // jsonb

@Column(columnDefinition = "text")
private String cycleDefinition = "";    // text DEFAULT ''

private Integer cycleSkipUntil = 0;     // int DEFAULT 0
```

> 이 컬럼들은 AI 서버가 관리. Spring은 읽기 전용으로 사용.
> `eftStage` → 프론트에 단계 표시, `cycleDefinition` → 사이클 동의 UI

#### 1-2. MediationRecord 엔티티 — ai_response 추가

```java
// 기존 필드에 추가
@Column(columnDefinition = "text")
private String aiResponse;  // nullable — AI 처리 전에는 NULL
```

#### 1-3. MediationReport 엔티티 — 신규 생성

```java
@Entity
@Table(name = "mediation_reports")
public class MediationReport extends BaseTimeEntity {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long reportId;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "session_id", nullable = false)
    private MediationSession session;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(columnDefinition = "text", nullable = false)
    private String emotionSummary;

    @Column(columnDefinition = "text", nullable = false)
    private String partnerUnderstanding;

    @Column(columnDefinition = "text", nullable = false)
    private String mediationPlans;

    @Column(columnDefinition = "text", nullable = false)
    private String recommendedDialogues;
}
```

---

### 2. AI 서버 HTTP 클라이언트 구현

AI 서버(Python FastAPI)에 HTTP 요청을 보내는 서비스 작성.

#### 2-1. DTO 작성

```java
// === Request DTOs ===

public class RoundAnalyzeRequest {
    private Long sessionId;    // session_id
    private String fReply;     // f_reply
    private String mReply;     // m_reply
}

public class CycleRequest {
    private Long sessionId;
    private String fExploreAnswer;  // 빈 문자열이면 탐색 모드
    private String mExploreAnswer;
}

public class ReportRequest {
    private Long sessionId;
}

// === Response DTOs ===

public class RoundAnalyzeResponse {
    private Long sessionId;
    private String fMessage;
    private String mMessage;
    private boolean needsCycleDefinition;
    private boolean riskFlag;
    private Integer eftStage;       // 현재 단계(1/2/3) — DB에도 갱신됨
    private Integer stageProgress;  // 진행도 0~100 — 3단계 90 도달 시 종료
}

public class CycleExploreResponse {
    private Long sessionId;
    private String fQuestion;
    private String mQuestion;
}

public class CycleDefinitionResponse {
    private Long sessionId;
    private String cycleDefinition;
    private String fMessage;   // 추가 — 여성에게 전달할 상담사 브릿지 메시지 (빈 문자열 가능)
    private String mMessage;   // 추가 — 남성에게 전달할 상담사 브릿지 메시지 (빈 문자열 가능)
}

public class ReportSections {
    private String emotionSummary;
    private String partnerUnderstanding;
    private String mediationPlans;
    private String recommendedDialogues;
}

public class ReportResponse {
    private Long sessionId;
    private ReportSections fReport;
    private ReportSections mReport;
}
```

> JSON 필드명이 snake_case이므로 Jackson 설정 또는 `@JsonProperty` 필요:
> ```java
> @JsonProperty("session_id")
> private Long sessionId;
> ```

#### 2-2. AI 서버 클라이언트 서비스

```java
@Service
public class AiServerClient {
    private final RestClient restClient;

    public AiServerClient(@Value("${ai.server.url}") String aiServerUrl) {
        this.restClient = RestClient.builder()
            .baseUrl(aiServerUrl)
            .build();
    }

    public RoundAnalyzeResponse roundAnalyze(Long sessionId, String fReply, String mReply) {
        return restClient.post()
            .uri("/ai/round-analyze")
            .body(new RoundAnalyzeRequest(sessionId, fReply, mReply))
            .retrieve()
            .body(RoundAnalyzeResponse.class);
    }

    public CycleExploreResponse cycleExplore(Long sessionId) {
        return restClient.post()
            .uri("/ai/cycle")
            .body(new CycleRequest(sessionId, "", ""))
            .retrieve()
            .body(CycleExploreResponse.class);
    }

    public CycleDefinitionResponse cycleDefine(Long sessionId, String fAnswer, String mAnswer) {
        return restClient.post()
            .uri("/ai/cycle")
            .body(new CycleRequest(sessionId, fAnswer, mAnswer))
            .retrieve()
            .body(CycleDefinitionResponse.class);
    }

    public ReportResponse generateReport(Long sessionId) {
        return restClient.post()
            .uri("/ai/report")
            .body(new ReportRequest(sessionId))
            .retrieve()
            .body(ReportResponse.class);
    }
}
```

#### 2-3. application.yml 추가

```yaml
ai:
  server:
    url: http://banuzil-ai.duckdns.org
```

---

### 3. MediationService 수정 — AI 연동

현재 `submitRecord()` 흐름을 수정하여 양측 제출 완료 시 AI 서버를 호출한다.

#### 현재 흐름

```
양측 발화 제출 → record 저장 → 3라운드면 COMPLETED
```

#### 변경 후 흐름

```
양측 발화 제출 → record 저장 → AI 서버 호출 → AI 응답 전달
                                            → needs_cycle_definition이면 사이클 처리
                                            → risk_flag이면 위험 알림
                                            → 종료 조건이면 보고서 생성
```

#### 핵심 변경 포인트

**`submitRecord()` 메서드 내 "양측 제출 완료" 분기:**

```java
// 기존: 단순 라운드 체크
if (round >= 3) {
    session.complete();
}

// 변경: AI 서버 호출
if (bothSubmitted) {
    // 여성/남성 record에서 content 추출
    String fReply = femaleRecord.getContent();
    String mReply = maleRecord.getContent();

    // AI 서버 호출
    RoundAnalyzeResponse aiResponse = aiServerClient.roundAnalyze(
        session.getSessionId(), fReply, mReply
    );

    // AI 메시지를 각 사용자에게 전달 (프론트로)
    // → aiResponse.getFMessage(), aiResponse.getMMessage()

    // 사이클 정의 필요?
    if (aiResponse.isNeedsCycleDefinition()) {
        // → 아래 "사이클 처리 흐름" 참고
        startCycleProcedure(session.getSessionId());
    }

    // 위험 감지?
    if (aiResponse.isRiskFlag()) {
        // 위험 알림 처리
    }

    // 라운드 진행 (3라운드 고정 → AI 서버가 EFT 단계로 관리)
    session.advanceRound();
}
```

#### 종료 조건 변경

- **기존**: 3라운드 고정 → COMPLETED
- **변경**: AI 서버의 EFT 단계가 3단계이고 `stage_progress >= 90`이면 종료 가능
- 종료 시 `POST /ai/report` 호출하여 보고서 생성

```java
// 종료 조건 체크 (DB에서 읽기)
MediationSession session = sessionRepository.findById(sessionId);
if (session.getEftStage() == 3 && session.getStageProgress() >= 90) {
    // 보고서 생성
    ReportResponse report = aiServerClient.generateReport(sessionId);
    // 프론트에 보고서 전달
    session.complete();
}
```

#### 사이클 처리 흐름 (needs_cycle_definition == true 분기)

`/ai/cycle`은 같은 엔드포인트를 **두 번** 호출하는 순차 절차다.

```
[1단계 — 탐색 질문 받기]

POST /ai/cycle  ← f_explore_answer / m_explore_answer 없이 session_id만 전달
   응답: CycleExploreResponse
     - f_question: 여성에게 보여줄 상담사 탐색 질문
     - m_question: 남성에게 보여줄 상담사 탐색 질문

Spring: 각 내담자 화면에 탐색 질문 표시, 답변 수집 대기
```

```java
CycleExploreResponse explore = aiServerClient.cycleExplore(sessionId);
// explore.getFQuestion() → 여성 화면에 표시
// explore.getMQuestion() → 남성 화면에 표시
```

```
[2단계 — 사이클 정의 + 브릿지 메시지 받기]

POST /ai/cycle  ← f_explore_answer + m_explore_answer 포함 전달
   응답: CycleDefinitionResponse
     - cycle_definition: 두 사람의 부정적 상호작용 패턴 정의문
     - f_message: 여성에게 전달할 상담사 브릿지 메시지 (2단계로 초대)
     - m_message: 남성에게 전달할 상담사 브릿지 메시지

Spring 처리:
  1. cycle_definition 양측에 표시 (공유 정보)
  2. f_message → 여성 화면에 상담사 말풍선으로 표시
  3. m_message → 남성 화면에 상담사 말풍선으로 표시
  4. f_message / m_message → mediation_records INSERT (아래 참고)
  5. 다음 라운드 진행 → AI 서버가 자동으로 2단계 전환 (Spring 별도 처리 없음)
```

```java
CycleDefinitionResponse def = aiServerClient.cycleDefine(sessionId, fAnswer, mAnswer);

// 표시
showCycleDefinition(def.getCycleDefinition());
showAiMessage("f", def.getFMessage());
showAiMessage("m", def.getMMessage());

// mediation_records INSERT (브릿지 히스토리 연속성)
// content = NULL, ai_response = 메시지, round_number = 직전 완료 라운드
if (!def.getFMessage().isEmpty()) {
    recordRepository.insertBridge(sessionId, fUserId, def.getFMessage(), prevRound);
}
if (!def.getMMessage().isEmpty()) {
    recordRepository.insertBridge(sessionId, mUserId, def.getMMessage(), prevRound);
}
```

> `round_number`는 **직전 완료 라운드** (`current_round - 1`)로 넣어야 한다.
> 다음 라운드 round-analyze 처리 시 AI 서버가 `round_number = 다음 라운드`로 UPDATE하는데,
> 브릿지를 같은 번호에 넣으면 덮어써진다.

---

### 4. 새 API 엔드포인트 (프론트엔드 연동)

Spring이 프론트엔드에 제공해야 하는 새 엔드포인트:

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/sewings/{sessionId}/report` | 보고서 조회 (mediation_reports에서 SELECT) |
| POST | `/api/sewings/{sessionId}/cycle/explore` | 사이클 탐색 질문 요청 |
| POST | `/api/sewings/{sessionId}/cycle/define` | 사이클 정의 생성 (답변 전달) |

> ~~`/cycle/agree`~~ 별도 동의 엔드포인트는 **불필요**. 사이클 정의가 저장되면
> 다음 라운드의 round-analyze에서 AI 서버가 자동으로 2단계로 전환한다(eft_stage를 Spring이 쓰지 않음).

---

### 5. 여성/남성 구분

AI 서버는 `users.gender` 컬럼으로 여성/남성을 구분한다.

- `gender = 'female'` → f_reply, f_message
- `gender = 'male'` → m_reply, m_message

Spring에서 양측 발화를 AI에 보낼 때, 여성 유저의 content를 `f_reply`에, 남성 유저의 content를 `m_reply`에 매핑해야 한다.

```java
User initiator = session.getInitiator();
User participant = session.getParticipant();

String fReply, mReply;
if ("female".equals(initiator.getGender())) {
    fReply = initiatorRecord.getContent();
    mReply = participantRecord.getContent();
} else {
    fReply = participantRecord.getContent();
    mReply = initiatorRecord.getContent();
}
```

---

## 작업 우선순위

| 순서 | 작업 | 이유 |
|------|------|------|
| 1 | DB 마이그레이션 (SQL 실행) | 다른 작업의 전제 조건 |
| 2 | 엔티티 수정 (Session, Record, Report) | JPA 매핑 |
| 3 | AI 서버 클라이언트 (AiServerClient) | 핵심 연동 |
| 4 | MediationService 수정 | 기존 흐름에 AI 호출 삽입 |
| 5 | 프론트 API (보고서 조회, 사이클 처리) | UI 연동 |

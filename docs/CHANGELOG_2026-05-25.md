# 변경 이력 — 2026-05-25

> 사이클 정의 흐름 개선, 자동테스트 리얼화, 상담사 응답 변주 작업 정리.
> 관련 커밋: `74d110c`, `8c67e65`, `2848bb4` (base: `72d580b`)

---

## 1. 사이클 정의 직후 상담사 브릿지 메시지 (신규 기능)

**문제:** `/ai/cycle` 정의 모드가 `cycle_definition`만 반환 → 사이클 정의가 끝나면
내담자에게 줄 상담사 발화가 없어, 사용자가 무엇에 응답할지 알 수 없었다.

**변경:**
- `CycleDefinitionResponse`에 `f_message`/`m_message` 추가 ([schemas/response.py](../schemas/response.py))
- `build_cycle_bridge_message_prompt()` 신규 ([config/prompts/eft_base.py](../config/prompts/eft_base.py)) —
  EFT Step 4(문제 재구성): 명명된 사이클을 당신 어조로 비춰주고, 1차 감정·애착 욕구를
  공감적 추론으로 짚으며 2단계로 부드럽게 초대
- `/ai/cycle` define 분기에서 f/m 브릿지를 `MODEL_NAME`으로 병렬 생성·반환
  ([routers/counseling.py](../routers/counseling.py)). 실패 시 빈 문자열 fallback
- **Request 스키마 변경 없음 / Response만 확장**

**스프링 대응:** 브릿지 메시지 INSERT 주체는 스프링(기존 계약 유지). 상세는
[SPRING_MIGRATION_CYCLE_BRIDGE.md](SPRING_MIGRATION_CYCLE_BRIDGE.md) 참고.
- `mediation_records`에 2행 INSERT: `content=NULL`, `ai_response=f/m_message`,
  `round_number=직전 완료 라운드`
- 대화창 재조회 렌더 시 content 빈 행은 유저 말풍선 생략
- AI 서버 히스토리 재조립은 코드 변경 없이 자동 포함

---

## 2. 사이클 정의 프롬프트 자연스러움 보강

`build_cycle_definition_prompt()` ([config/prompts/eft_base.py](../config/prompts/eft_base.py))

- **메타 문장 제거:** "두 사람 모두 피해자임을 강조하라" 지시가 출력에 새어나와
  "…강조할 수 있습니다"로 끝나던 문제 → 어조 규칙으로 전환 + 메타·결론 문장 금지
- **일회성 사건 분리:** 일회성 사건(예: 특정 여행 일정)을 반복 고리에 넣지 말고
  '계기'로만 도입. 반복되는 건 추격-위축 감정 악순환임을 명시
- **고리 닫기:** 고리가 한 바퀴 돌아 처음 반응으로 이어지도록 닫기('반복됩니다'가 자연스럽게)
- **탓 뉘앙스 제거:** "~해버리다" 등 한쪽을 탓하는 표현 금지, 중립 묘사

---

## 3. EFT 단계 페이싱 — 1단계 강제 진입 단축

[graphs/eft_graph.py](../graphs/eft_graph.py)

- `CYCLE_FORCE_ROUNDS` **4 → 3**: 1단계 신호가 양측 2개씩 안 차도, 누적 3라운드부터
  사이클 절차로 강제 진입(철회형 커플의 1단계 정체 방지). 체감: `needs_cycle_definition`이
  한 라운드 일찍 올 수 있음. **스프링 대응 불필요**(단계 전환은 AI 서버 전담).

> 참고 — 진행도(progress) 출처: **3단계는 코드 점증**(Step8 40·58·75 / Step9 78·90·100),
> **1·2단계는 eval 모델 반환값**. (2단계가 0%로 멈춰 보이는 건 eval 모델이 0을 주기 때문)

---

## 4. 상담사 응답 단조로움 완화 (변주 규칙)

`build_system_prompt` 행동지침·출력규칙 + `build_user_message` 일반 분기
([config/prompts/eft_base.py](../config/prompts/eft_base.py))

- **매 라운드 "그분은 ~"으로 시작 금지** — 내담자 감정 먼저 받기/상대 전달 위치·길이 변경
  허용. 단 상대 발화 전달 자체는 유지(중재 도구의 핵심 기능)
- **"혹시 ~한 마음" 추론 표현 절제** — 한 응답 1회 이하, "~처럼 들려요" 등 표현 다양화,
  매 라운드 반복 금지 (시스템 프롬프트·출력규칙·user 메시지 3곳 반영)
- **응답 길이 변주** — 매번 3문단 대신 2~4문장으로 짧게 끝내는 라운드 허용
- 질문은 여전히 기본값(턴제 대화의 진행 원동력). 다만 "요약→질문" 골격의 *기계적 반복*만 방지

---

## 5. 자동테스트(test_client.html) 개선

[test_client.html](../test_client.html)

- **사이클 정의문 로그:** 수동 모드에서도 정의문을 채팅·요청로그에 기록(기존엔 자동 모드만)
- **브릿지 메시지 표시:** `applyCycleBridge()` 공용 헬퍼 — 정의 직후 f/m 브릿지 표시 +
  (mock) 스프링 역할로 records INSERT(연속성 재현)
- **EFT 8신호 시각화:** 우측 패널에 여/남 각 `n/8` + 신호 칩(1단계 파랑·2단계 보라, 누적).
  매 라운드 후 `refreshSignals()`로 갱신
- **시뮬레이션 내담자 말투 리얼화(`SIM_TALK_STYLE`):** 반말/거친 구어체 + 중강도 에고
  (저항·비꼼·회피·"아 몰라" 허용, 욕설 제외). 정중한 요체 가이드 제거

**보조 API(테스트 전용):**
- `GET /ai/mock/get-session` 신규 — mock 세션 상태(detected_signals 등) 조회
  ([routers/mock_router.py](../routers/mock_router.py))
- `POST /ai/mock/add-record`에 `ai_response` 옵셔널 파라미터 추가(하위호환, 브릿지 영속화용)

---

## 6. 문서

- [SPRING_MIGRATION_CYCLE_BRIDGE.md](SPRING_MIGRATION_CYCLE_BRIDGE.md) 신규 — 스프링 대응 가이드
- [API_SPEC_FOR_SPRING.md](API_SPEC_FOR_SPRING.md) 갱신 — `/ai/cycle` 응답 필드 + INSERT 계약

---

## 7. 진행도 코드화 + 보고서 양식 통일 + 답변 유도형 마무리 (2차)

> 아래 1~6 적용 후 자동테스트에서 확인된 잔여 이슈 3건을 후속 반영.

**1·2단계 진행도 코드화** ([graphs/eft_graph.py](../graphs/eft_graph.py) `node_stage_transition_check`)
- eval 모델 반환값 의존을 제거하고, **해당 단계 신호(4종 × 양측 = 8칸) 누적 개수 기반**으로 계산
  → `progress = min(90, round(on/8*100))` (0→0%, 3개→38%, 8개→90%)
- 이제 1·2·3단계 진행도가 모두 코드 산출(2단계가 0%로 멈춰 보이던 문제 해소)

**답변 유도형 마무리** ([config/prompts/stage_prompts.py](../config/prompts/stage_prompts.py) Step 8·9 + [eft_base.py](../config/prompts/eft_base.py))
- 3단계가 매 라운드 전체 변화 서사를 통째로 재요약하던 긴 모놀로그 → **금지**
- 대신 내담자가 직접 답할 수 있는 질문 하나로 마무리(예: "오늘 가장 마음에 남는 한 가지는?",
  "다음에 그 순간이 오면 어떤 한마디를 건네보고 싶어요?") — 내담자는 물어봐야 답한다
- 일반 라운드도 "대부분 답할 거리(열린 질문·구체적 초대)로 마무리"를 기본값으로(변주는 유지)

**보고서 양식 통일** ([config/prompts/eft_base.py](../config/prompts/eft_base.py) `build_report_prompt`)
- f/m 보고서가 별도 LLM 호출이라 포맷이 달랐던 문제 → 출력 형식을 못박음
- 중재안: `1. 제목 / 필요한 이유: / 실천 포인트 / - 항목` 고정
- 추천 대화: 큰따옴표 완성 문장만, **"당신:"/"나:" 화자 접두사 금지**(채널 간 불일치 제거)
- 번호 "1./2./3.", 목록 "- ", 항목 줄바꿈 분리 등 형식 일관성 규칙 명시

> 진행도 출처(갱신): **1·2·3단계 모두 코드 산출.** 1·2단계는 신호 개수 기반, 3단계는 Step별 점증.

---

## 검증 상태

- ✅ import/부팅, 스키마·라우터 배선, 프롬프트 변경 결정론 확인
- ✅ mock 자동테스트 end-to-end 실행(연락 갈등/일본 여행 시나리오):
  사이클 정의→브릿지→1→2→3단계 전환→90% 종료→보고서 정상
- ✅ 변주 규칙 효과 확인(1·2단계에서 "그분은" 매번 시작·"혹시~" 반복 감소)
- ✅ 채널 간 상대 발화 전달 정확(환각 없음)

## 잔여 이슈 처리 현황

1. ~~보고서 양식 불일치~~ → **해결**(7장: `build_report_prompt` 형식 고정 + 접두사 금지)
2. ~~3단계 메타 요약 반복~~ → **해결**(7장: 답변 유도형 마무리로 전환)
3. ~~2단계 진행도 0% 정체~~ → **해결**(7장: 신호 기반 코드 산출)

> 위 7장 변경은 실LLM 자동테스트로 효과 재확인 필요(이 환경은 결정론 검증까지 완료).

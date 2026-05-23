# EFT 상담 프롬프트 & 진행 로직 개선 (2026-05-23)

> 사용자 피드백 3건을 EFT(정서중심치료) 기법에 근거해 반영하고,
> 단계 전환이 막혀 있던 핵심 버그를 수정한 작업 정리.

---

## 1. 배경 — 사용자 피드백

| # | 피드백 | 분류 |
|---|--------|------|
| 1 | 라운드 진행이 너무 느리다 / 단계가 안 넘어간다 (목표 ~12라운드) | 진행 로직 |
| 2 | 매 라운드 "상대 요약 + 질문"만 반복돼 단조롭다 | 프롬프트 |
| 3 | 상대 발화 요약이 무미건조하다. 상대 감정에 대한 따뜻한 분석이 필요하다 | 프롬프트 |

---

## 2. 핵심 진단 — 단계(stage) 전환 버그

### 라운드(round) ≠ 단계(stage)
- **라운드** `current_round`: 대화 횟수. `save_round_result`가 매 라운드 +1 → **정상 작동**.
- **단계** `eft_stage` (1→2→3): EFT 국면. **1에 영구 고정되는 버그 존재**.

### 1→2 전환이 코드에 없었음
- `graphs/eft_graph.py` `node_stage_transition_check`: `if new_stage==2 and stage==1: new_stage=1` 로 **무조건 차단**.
- 주석/프롬프트는 "사이클 동의 절차가 처리"라고 했으나 **그 코드가 어디에도 없었음**.
- `cycle_agreed` State 필드는 선언만 되고 아무도 안 읽음. DB 컬럼·전달 경로 없음.
- **Spring 레포(`2026-1-CSC4004-2-2-Cider`) 확인**: `MediationService.defineCycle`은 정의 생성·반환만, `eft_stage` 안 씀. "라운드 증가는 AI 서버가 전적으로 책임짐" 주석. 동의 엔드포인트/컬럼 없음.
- → **시스템 전체에 '동의' 신호 통로가 없음**. 따라서 AI 서버가 `cycle_definition` 존재를 진입 트리거로 1→2를 자동 전환하는 것이 유일한 설계.

### 기존 test html이 "올린 것처럼" 보였던 이유
- test_client.html은 `eft_stage`를 표시한 적이 없음 (round-analyze 응답에 필드 자체가 없었음).
- "2단계 진입"은 사이클 버튼 클릭 시 뜨는 **하드코딩 텍스트(addSys)**였을 뿐, 실제 DB `eft_stage`는 1로 고정.

---

## 3. 변경 사항

### Part A — 라운드 진행 (#1)

**A1. 1→2 전환 구현 (핵심 버그 수정)** — `graphs/eft_graph.py` `node_stage_transition_check`
- 무조건 차단을 조건부 전환으로 교체:
  `stage==1` & `cycle_definition` 존재 & `stage_rounds["1"] >= MIN_STAGE_ROUNDS` → **2단계 진입**.
- 그 외 1단계 유지(eval 모델이 함부로 못 올림). 2→3는 기존대로 신호 + 최소 라운드 게이트.

**A2. 진행 조건 완화 (~12라운드 페이싱)** — `config/prompts/stage_prompts.py`
- 1단계 step 하드캡 완화: `라운드≤2→step1, ≤3→step2` → **라운드1만 step1 고정**, 이후 신호 기반.
- 1단계 공감 고정 블록: `라운드≤2` → **라운드1만** (신호 유도를 라운드2부터 시작).
- 2→3 신호 임계 완화: 양측 **"4개 중 3개" → "2개 이상"**.
- 3단계 progress 가이드: 라운드별 +25~30 점증 → ~11-12R에 90 도달 (Spring 종료 트리거 `eft_stage==3 && progress>=90`와 정합).
- 목표 분포: 1단계 ~4-5R / 2단계 ~4-5R / 3단계 ~3R.

### Part B — EFT 기법 기반 응답 개선

**B1. 단조로움 해소 (#2)** — `config/prompts/eft_base.py`
- `build_user_message`의 고정 "요약① → 질문②" 골격을 **EFT 개입 레퍼토리 + 라운드별 변주**로 교체:
  반영(Reflection)·타당화(Validation)·환기적 반응(Evocative)·심화(Heightening)·공감적 추론(Empathic Conjecture) 중 그 라운드 정서 흐름에 맞는 1~2개 선택.
- **매 라운드 질문 강제 해제** — 반영·타당화·심화로 마무리해도 됨.
- `build_system_prompt` [상담사 행동 지침]에 변주 원칙 + EFT Tango 흐름 + RISSSC 톤 추가.

**B2. 상대 감정의 따뜻한 재구성 (#3)** — `config/prompts/eft_base.py`
- 상대 감정 해석을 전면 금지하던 두 규칙(시스템 출력규칙 ②, user `prohibit_note` ②)을 **EFT 공감적 추론 허용**으로 교체.
- 상대의 1차 감정·애착 욕구를 *조심스러운 추론형*("혹시 ~한 마음이 아니었을까요")으로 따뜻하게 재구성. 단정형 금지, 양측 모두 애착 렌즈(악인화 금지).

**B3. 사이클 제안 라운드 — 질문 억제 확인**
- `is_cycle_round` 배선 정상(router→response_generator→`build_user_message` cycle 규칙 ③ "질문 던지지 마라").
- cycle 규칙에도 공감적 추론 톤 일관 적용.

**B4. 총알잡기 통합 (#재검토)** — `config/prompts/eft_base.py` + `graphs/eft_graph.py`
- `build_user_message`에 `bullet_detected` 분기 추가 → 총알 감지 시 **타당화·재구성(de-escalation) 우선, 탐색 질문 생략 가능**.
- `node_response_generator`에서 `bullet_detected` 전달. 우선순위 **bullet > cycle > 일반**.
- `suggested_intervention`(Closed Validation 등)이 이미 EFT 기법이라 B1 레퍼토리와 자연 통합.

**B5. 중립검사 오탐 방지 (#재검토)** — `config/prompts/neutrality_check.py` + `services/dspy_modules/eval_module.py`
- 중립성은 두 곳(전용 노드 + self_refine 6척도)에서 평가되며 둘 다 "양측 공정성"만 봄.
- 대칭적 공감적 추론은 편향/단정이 아님을 양쪽 프롬프트에 명시 (오탐 방지). B2는 오히려 `validation_depth`·`cycle_reframing` 점수를 높여 시너지.

### Part C — 진행 상태 가시화 (테스트용)

**C1. 응답에 단계 정보 추가** — `schemas/response.py` + `routers/counseling.py`
- `RoundAnalyzeResponse`에 `eft_stage`, `stage_progress` 추가 (기본값 있는 옵셔널 → Spring 무시 가능).
- round-analyze 응답에 실제 단계/진행도 반환.

**C2. test_client.html 단계 표시**
- 상단에 **단계 칩**(`pillStage`)·**진행도 칩**(`pillProgress`) 추가.
- 응답의 `eft_stage`/`stage_progress`를 읽어 표시, 단계 상승 시 시스템 메시지로 알림.
- 오해를 부르던 "2단계 진입" 가짜 텍스트를 실제 동작 설명으로 교체.

---

## 4. 변경 파일 목록

| 파일 | 변경 |
|------|------|
| `graphs/eft_graph.py` | A1 1→2 전환 로직, B4 bullet 인자 전달 |
| `config/prompts/stage_prompts.py` | A2 step캡·공감블록·2→3 임계·progress·1→2 문구 |
| `config/prompts/eft_base.py` | B1 변주/Tango/RISSSC, B2 공감적 추론, B4 bullet 분기 |
| `config/prompts/neutrality_check.py` | B5 공감적 추론 허용 주의사항 |
| `services/dspy_modules/eval_module.py` | B5 neutrality 기준 보강 |
| `schemas/response.py` | C1 eft_stage·stage_progress |
| `routers/counseling.py` | C1 응답 단계 정보 채움 |
| `test_client.html` | C2 단계/진행도 칩, 응답 반영, 텍스트 수정 |

---

## 5. 검증

- ✅ 임포트/부팅 정상.
- ✅ `build_user_message` 3분기(bullet/cycle/일반) + 우선순위(bullet>cycle) 정상.
- ✅ 단계 전환 로직 결정론 테스트 7/7: 1→2(cycle+2R 상승 / cycle없음·1R 유지), 2→3(2R 상승 / 1R 유지), 사이클 라운드 트리거/미트리거.
- ⏳ 응답 품질 정성 점검(OpenAI 키 필요): 변주·따뜻한 추론·총알 de-escalation·중립성 통과 육안 확인.

### 로컬 테스트 방법 (test_client.html)
1. 서버 실행 후 `/test` 접속, Mock DB 모드로 세션 시작.
2. 라운드를 반복 제출 → 상단 **단계 칩**이 1단계 유지되는지 확인.
3. `needs_cycle_definition` 뜨면 사이클 탐색→정의 진행 (cycle_definition 저장).
4. 다음 라운드 제출 → **단계 칩이 2단계로 전환**되면 A1 정상.
5. 이후 라운드에서 2→3 전환, 진행도 90% 도달까지 확인.

---

## 6. EFT 기법 근거 (리서치)

- **6개 정서 개입**: Reflection(반영)·Validation(타당화)·Evocative Responding(환기적 반응)·Heightening(심화)·Empathic Conjecture(공감적 추론)·RISSSC.
- **RISSSC**: Repeat·Images·Simple·Slow·Soft·Client's words — 따뜻하고 느린 톤으로 취약성 심화.
- **Empathic Conjecture**: 내담자가 언어화 못 한 1차 감정을 상담사가 조심스럽게 추론·명명 → 피드백 #3의 정답.
- **EFT Tango 5무브**: 현재 과정 반영 → 정서 조합·심화 → 교감 안무(enactment) → 교감 처리 → 통합·인정. 기계적 순서 아님.
- **Reframing**: 반응적 행동(비난·철회)을 애착 욕구/두려움으로 재구성, 양측 모두 사이클의 피해자(악인화 금지) → 중립성과 양립.

출처: Sue Johnson EFT (psychotherapy.net, ICEEFT), positivepsychology.com/emotion-focused-couples-therapy, wellrootscounseling.com (EFT Tango), carolinaeft.com (Core Skills).

---

## 6.5 2차 개선 (자동테스트 결과 반영)

1차 적용 후 자동테스트에서 발견된 문제들을 추가 수정.

### 단계 진행 (graphs/eft_graph.py, stage_prompts.py)
- **2→3 무한 정체 해소**: eval 모델(gpt-4o-mini)이 2단계 신호를 보수적으로 잡아 stage 2에 갇히고 3단계에 못 가 세션이 안 끝나던 문제. **코드 보조 게이트** 추가 — 2단계 누적 4R + 양측 깊은 신호 합 ≥2면 강제 3단계 진입(`STAGE2_FORCE_ROUNDS`).
- **eval 2→3 신호 판정 명확화**: 취약성/공감/재관여/연화를 어떤 발화에서 true로 잡을지 구체 기준 추가.
- **1단계 페이싱**: 사이클 진입 조건을 양측 신호 1개 이상 → 2개 이상으로 상향(2R 조기 종료 방지).
- **3단계 Step 8 → 9 분화**: `build_stage_instruction(stage3_round=...)`로 s3<3 → Step 8(일상 적용 합의), s3≥3 → Step 9(변화 서사 공고화 + 재발 예방).
- **진행도를 스텝과 연동**: 3단계 progress를 단순 +30씩에서 단계 분화형으로 교체. Step 8(s3<3): 40·58·75(상한)로 묶어 조기 종료 방지 / Step 9(s3≥3): 78·90·100 → 공고화 1~2R 후 90 도달 종료.

### 응답 품질 (config/prompts/eft_base.py)
- **"자기"·애칭 호칭 금지** + 상대 지칭 "상대분/그분"으로 통일.
- **대본 대필 억제**: 인액트먼트 시 완성 대사 대필 금지, 짧은 초대로 내담자가 스스로 말 찾게. 예시 한 줄 이내.
- **응답 2~3문단 제한** + 직전 라운드 핵심 문구 반복 금지.

### 자동테스트 (test_client.html)
- 실제 종료조건(eft_stage==3 && progress>=90) 도달 시 루프 자동 종료(기존엔 고정 라운드만 채움).
- 보고서 미표시 버그 수정: window.open 팝업 차단 + catch 에러 삼킴 → 페이지 내 오버레이로 표시 + 오류 노출.
- 라운드별 단계·진행도를 로그에 기록. 시뮬레이션 발화를 구어체·비정석으로(저항·망설임·점진적 변화 허용).

> 검증: mock 결정론 테스트(1→2, 2→3 eval/강제, 3단계 progress 점증, 사이클 2신호) 전수 통과. 실제 LLM 응답에서 2문단·자기 호칭 제거·대본 대필 0 확인. 자동테스트 1→2→3→자동종료→보고서 전 흐름 정상.

---

## 7. 범위 밖 / 추후

- 사이클 "거부" 흐름은 Spring/AI 양쪽 미구현 → 현 구현은 정의 생성=진입 간주.
- 페이싱 수치(2→3 신호 개수, progress 증가율)는 실사용 데이터로 미세조정 여지.
- (참고) `settings.py` EVAL_WEIGHTS neutrality=0.30 vs `eval_module` 프롬프트 "20%" 문구 불일치 — 사소.

# 바느질 AI 서버 — 코드 분석

핵심 모듈별 실제 코드와 동작 원리 설명.

---

## 1. `services/llm.py` — LLM 호출 인터페이스

```python
def _make_llm(model, max_tokens) -> ChatOpenAI:
    return ChatOpenAI(model=model or MODEL_NAME, temperature=TEMPERATURE, max_tokens=...)

async def call_llm(system_prompt, user_message, model, max_tokens) -> str:
    llm = _make_llm(model, max_tokens)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
    response = await llm.ainvoke(messages)
    return response.content

async def call_llm_with_history(system_prompt, history, user_message, model, max_tokens) -> str:
    llm = _make_llm(model, max_tokens)
    messages = [SystemMessage(content=system_prompt)]
    for h in history:
        messages.append(HumanMessage(content=h["content"]) if h["role"] == "user"
                        else AIMessage(content=h["content"]))
    messages.append(HumanMessage(content=user_message))
    response = await llm.ainvoke(messages)
    return response.content
```

**역할:** 전체 프로젝트의 단일 LLM 호출 창구. 모든 GPT 호출은 이 두 함수를 통한다.

- `call_llm` — system + user 1회성 호출. stage_transition_check 등 stateless 작업에 사용.
- `call_llm_with_history` — system + 누적 히스토리 + user 호출. 상담 응답 생성(response_generator)에 사용.
- bullet_detector / self_refine / neutrality_check 는 DSPy 모듈로 분리되어 이 파일을 거치지 않는다.

---

## 2. `graphs/eft_graph.py` — EFT 상태 그래프

### 2-1. EFTState

```python
class EFTState(TypedDict):
    # 세션 고정
    session_id: str
    couple_profile: Any          # CoupleProfile (ECR-R 기반 32분류)

    # 설정 (런타임 오버라이드 가능)
    model_name: str
    bullet_enabled: bool
    emotion_weight: float

    # 현재 라운드 입력
    f_reply: str                 # 여성 발화
    m_reply: str                 # 남성 발화
    round_num: int

    # 대화 히스토리
    f_history: list[dict]        # [{"role": "user"|"assistant", "content": str}]
    m_history: list[dict]

    # EFT 상태
    eft_stage: int               # 1 / 2 / 3
    stage_rounds: dict[int, int] # {1: n, 2: n, 3: n} — 단계별 누적 라운드
    stage_progress: int          # 0~100
    signals: SignalState         # EFT 진행 신호 누적 (단방향)
    cycle_definition: str
    needs_cycle_definition: bool

    # KoELECTRA 감성 결과
    f_emotion_data: Optional[dict]
    m_emotion_data: Optional[dict]

    # 총알잡기
    bullet_detected: bool
    bullet_type: str             # "Reactive" / "Mistrust" / "None"
    bullet_target: str           # "f" / "m" / ""
    bullet_intervention: str

    # 생성 결과
    f_response: str
    m_response: str

    # Self-Refine
    refine_count: int
    eval_scores: dict
    refine_feedback: str

    # 중립성 검사
    neutrality_result: Optional[dict]
    neutrality_warning: str      # 다음 라운드 프롬프트에 삽입할 경고

    # 위험 신호
    risk_flag: bool
    risk_category: str
    risk_keywords_found: list[str]
```

**설계 의도:** LangGraph는 노드 간 데이터를 State dict 하나로 전달한다. 모든 노드는 `{**state, "변경된 필드": 새값}` 형태로 State를 업데이트해서 반환한다. 노드가 서로의 내부 구현을 모르고 State만 읽고 쓰기 때문에 노드 교체/추가가 쉽다.

---

### 2-2. 노드 1: `node_risk_gate` — 위험 키워드 감지

```python
_RISK_KEYWORDS_HARD = {
    "자해":     ["자해", "손목", "긋"],
    "자살":     ["자살", "죽고 싶", "뛰어내리", "목매", "극단적 선택"],
    "폭행":     ["때리", "죽이", "폭행", "칼로", "협박", "죽여"],
    "스토킹":   ["스토킹", "신상", "감금", "따라다"],
    "데이트폭력": ["강제로", "강간", "성폭"],
}

def node_risk_gate(state):
    combined = state["f_reply"] + " " + state["m_reply"]
    found_keywords = []
    for category, keywords in _RISK_KEYWORDS_HARD.items():
        for kw in keywords:
            if kw in combined:
                found_keywords.append(kw)
                risk_category = category
    return {**state, "risk_flag": len(found_keywords) > 0, ...}

def edge_risk_gate(state) -> str:
    return "END" if state["risk_flag"] else "bullet_detector"
```

**설계 의도:** LLM 없이 순수 문자열 매칭으로 동작한다. 위험 키워드 감지는 LLM 응답 지연(1~3초)을 기다릴 수 없고 오탐을 허용해서도 안 된다. IPV 위험 결합(`ipv_risk_flag=True`)은 임계값을 더 낮게 설정해 더 민감하게 반응한다.

감지 즉시 `edge_risk_gate`에서 `END`로 빠져나가고 이후 노드는 실행되지 않는다.

---

### 2-3. 노드 2: `node_bullet_detector` — 총알잡기 (DSPy)

```python
async def node_bullet_detector(state):
    if not state.get("bullet_enabled"):
        return {**state, "bullet_detected": False, "bullet_type": "None"}

    from services.dspy_modules.bullet_module import get_bullet_detector
    detector = get_bullet_detector()
    loop = asyncio.get_running_loop()

    # DSPy Predict는 동기 → run_in_executor로 병렬 실행
    f_res, m_res = await asyncio.gather(
        loop.run_in_executor(None, lambda: detector.forward(f_reply, eft_stage, True)),
        loop.run_in_executor(None, lambda: detector.forward(m_reply, eft_stage, False)),
    )
    # threshold 이상 신뢰도만 총알로 판정
    f_bullet = f_res.get("bullet_detected") and f_res.get("confidence") >= threshold
    ...
```

**총알 유형:**
| 유형 | 의미 | AI 대응 |
|------|------|---------|
| `Reactive` | 즉각적 비난·공격 | 즉시 타당화(Closed Validation) 후 재구성 |
| `Mistrust` | 불신·냉소적 거부 | 부드러운 공감 + 변화 두려움 인정 |

DSPy `BulletDetector.forward()` 반환값: `{bullet_detected:bool, bullet_type:str, confidence:float, suggested_intervention:str}`  
결과는 State에 저장되고 `node_response_generator`에서 프롬프트에 주입된다.

---

### 2-4. 노드 3: `node_emotion_inject` — KoELECTRA 감성 분석

```python
async def node_emotion_inject(state):
    service = EmotionService()  # 싱글턴 — 모델 재로드 없음
    loop = asyncio.get_running_loop()

    # CPU 블로킹 연산을 스레드풀에서 실행 (이벤트 루프 차단 방지)
    f_result, m_result = await asyncio.gather(
        loop.run_in_executor(None, lambda: service.analyze(
            text=state["f_reply"], gender="여성", situation=...)),
        loop.run_in_executor(None, lambda: service.analyze(
            text=state["m_reply"], gender="남성", situation=...)),
    )
    return {**state, "f_emotion_data": f_result, "m_emotion_data": m_result}
```

**설계 의도:** PyTorch 추론은 CPU 블로킹 연산이라 `await`가 없으면 이벤트 루프가 멈춘다. `run_in_executor`로 스레드풀에서 실행해 비동기 처리한다. 실패해도 `None`으로 graceful fallback — 감성 분석 오류가 상담을 중단시키지 않는다.

결과 형식:
```json
{
  "category": [{"rank": 1, "label": "슬픔", "score": 0.68}, ...],
  "detail":   [{"rank": 1, "label": "외로운", "score": 0.45}, ...]
}
```

---

### 2-5. 노드 5: `node_response_generator` — 상담 응답 생성 (핵심)

```python
async def node_response_generator(state):
    # 1) 프롬프트 조립
    f_sys = build_system_prompt(is_female=True, couple_profile=cp, eft_stage=stage, ...)
    m_sys = build_system_prompt(is_female=False, ...)

    # 2) 총알 컨텍스트 주입
    if state.get("bullet_detected"):
        bullet_context = f"총알 유형: {bullet_type} | 권장 개입: {bullet_intervention}\n→ 총알잡기 5단계 프로토콜 적용"

    # 3) 감성 컨텍스트 주입
    emotion_context = f"여성: 대분류 슬픔(0.68) | 소분류 외로운(0.45) ..."

    # 4) refine/neutrality 피드백 주입 (재생성 시)
    if refine_feedback or neutrality_feedback:
        f_stage_instruction += f"\n\n{feedback}"

    # 5) 여성/남성 동시 GPT 호출 (편향 방지 핵심)
    f_resp, m_resp = await asyncio.gather(
        _call_llm_with_history(f_sys, f_history, f_user_msg, ...),
        _call_llm_with_history(m_sys, m_history, m_user_msg, ...),
    )
```

**`asyncio.gather`로 동시 생성하는 이유:** 여성 응답을 먼저 생성하면 그 내용이 남성 응답에 영향을 줄 수 있다(편향). 동시에 독립적으로 생성해야 양측 응답이 서로 오염되지 않는다.

---

### 2-6. 노드 6: `node_self_refine` — 품질 평가 + 재생성 (DSPy)

```python
async def node_self_refine(state):
    from services.dspy_modules.eval_module import get_eft_evaluator
    evaluator = get_eft_evaluator()
    loop = asyncio.get_running_loop()

    # DSPy EFTEvaluator (동기) → run_in_executor
    scores = await loop.run_in_executor(None, lambda: evaluator.forward(
        f_reply=..., m_reply=..., f_response=..., m_response=...,
        classification=cp.classification,
        f_attach_type=cp.f_profile.attach_type,
        m_attach_type=cp.m_profile.attach_type,
        eft_stage=state["eft_stage"],
    ))

    # 가중 평균
    weighted_avg = sum(scores.get(k, 3.0) * w for k, w in EVAL_WEIGHTS.items())

    # 재생성 조건
    if scores["safety"] < SAFETY_GATE_SCORE or weighted_avg < EVAL_PASS_SCORE:
        feedback = f"[재생성 요청 — 미달 척도: {failed}]\n개선 방향: {hints}"
        return {**state, "refine_count": refine_count + 1, "refine_feedback": feedback}

    return {**state, "eval_scores": scores, "refine_feedback": ""}

def edge_self_refine(state) -> str:
    return "response_generator" if state["refine_feedback"] else "stage_transition_check"
```

**6개 척도와 가중치:**
| 척도 | 가중치 | 통과 기준 |
|------|--------|---------|
| neutrality | 20% | 4.0 |
| validation_depth | 20% | 4.0 |
| attach_coherence | 20% | 4.0 |
| cycle_reframing | 15% | 4.0 |
| actionability | 15% | 4.0 |
| safety | 10% | **3.0 (별도 하드게이트)** |

`safety`는 가중평균과 별도로 3.0 미만이면 무조건 재생성한다. 폭력·학대 정상화가 있는 응답은 평균이 높아도 통과할 수 없다. 최대 `MAX_REFINE`(기본 3)회까지 재생성한다.

---

## 3. `graphs/neutrality_graph.py` — 중립성 검사 (DSPy)

```python
async def node_llm_judge(state):
    from services.dspy_modules.neutrality_module import get_neutrality_judge
    judge = get_neutrality_judge()
    loop = asyncio.get_running_loop()

    # DSPy NeutralityJudge (동기) → run_in_executor
    result = await loop.run_in_executor(None, lambda: judge.forward(
        f_reply=state["f_reply"], m_reply=state["m_reply"],
        f_response=state["f_response"], m_response=state["m_response"],
        classification=cp.classification, eft_stage=state["eft_stage"],
    ))
    return {**state, **result}

async def run_neutrality_check(f_reply, m_reply, f_response, m_response, ...) -> dict:
    state = {...}
    state = await node_llm_judge(state)   # DSPy로 채점
    state = node_verdict(state)           # 점수 기반 pass/fail (LLM 없음)
    return {...}
```

**판정 흐름:**
```
score >= 4.0  →  clean pass
3.0 <= score < 4.0  →  pass + warning_hint (다음 라운드 프롬프트에 경고 삽입)
score < 3.0  →  fail → regen_triggered=True → response_generator 재실행
```

**LangGraph 서브그래프가 아닌 이유:** 서브그래프로 만들면 `EFTState ↔ NeutralityState` 변환 레이어가 필요하다. 노드가 2개뿐이라 직접 함수 호출이 더 단순하다.

---

## 4. `config/prompts/stage_prompts.py` — SignalState

```python
@dataclass
class SignalState:
    f: dict[str, bool]  # 여성 신호
    m: dict[str, bool]  # 남성 신호

    def merge(self, gender: str, new_signals: dict[str, bool]):
        target = self.f if gender == "f" else self.m
        for k, v in new_signals.items():
            if k in target and v is True:
                target[k] = True  # False로 되돌리는 코드가 없음 — 단방향
```

**신호 목록:**
| 신호 | 단계 | 의미 |
|------|------|------|
| `emotion` | 1단계 | 1차 감정 어휘 직접 사용 |
| `patternAware` | 1단계 | 자신의 반응 패턴 인식 |
| `otherSide` | 1단계 | 상대 행동의 이면 언급 |
| `relationConcern` | 1단계 | 관계 자체에 대한 걱정 |
| `vulnerability` | 2단계 | 취약성 직접 표현 |
| `empathy` | 2단계 | 상대에 대한 연민 |
| `recoveryWill` | 2단계 | 관계 회복 의지 |
| `newComm` | 2단계 | 새로운 소통 시도 |

1단계 신호 4개가 양측 모두에서 일정 수 이상 누적되면 `needs_cycle_definition=True` → Spring이 `/counseling/cycle` 호출.

---

## 5. 전체 실행 흐름 요약

```
POST /counseling/round
        │
        ▼
[counseling.py] 세션 복원 → create_initial_state()
        │
        ▼
[eft_graph.py] graph.ainvoke(state)
        │
   risk_gate ──── risk=True ──→ END (즉시)
        │
   bullet_detector (asyncio.gather: f+m 병렬)
        │
   emotion_inject (run_in_executor: CPU 비동기)
        │
   eft_stage_router
        │
   response_generator (asyncio.gather: f+m 병렬 GPT)
        │
   neutrality_check ─── fail ──→ response_generator (루프백)
        │ pass
   self_refine ─── 미달 ──→ response_generator (루프백)
        │ pass
   stage_transition_check (신호 누적 + 단계 전환 결정)
        │
       END
        │
        ▼
[counseling.py] 세션 저장 → CounselingRoundResponse 반환
```

**재생성 루프 상한:** `refine_count`가 `MAX_REFINE`(기본 3)에 도달하면 중립성 실패, 품질 미달 여부와 무관하게 현재 응답으로 통과한다. 무한 루프 방지.

---

## 6. `services/dspy_modules/` — DSPy 모듈

### 왜 DSPy를 쓰는가

| 기존 방식 | DSPy 방식 |
|-----------|-----------|
| `build_*_prompt()` 함수로 프롬프트 직접 작성 | `dspy.Signature`로 입출력 필드만 선언 |
| `call_llm()` 후 `json.loads(raw)` | `dspy.Predict(Signature)` → 타입된 필드로 바로 접근 |
| JSON 파싱 실패 시 예외 처리 코드 필요 | 내장 파싱 + 타입 검증 |
| 데이터 쌓이면 프롬프트 수동 조정 | `dspy.Optimizer`로 자동 튜닝 가능 |

**현재 상태:** 데이터가 없으므로 **기존 프롬프트가 기본값으로 그대로 사용된다.** 각 모듈은 `is_optimized` 플래그로 분기하며, `dspy_optimized/` 폴더에 최적화 파일이 있을 때만 DSPy Predict로 전환된다. 파일이 없으면 기존 `build_*_prompt() → LLM → json.loads()` 흐름이 100% 동일하게 동작한다.

### `services/dspy_modules/__init__.py` — LM 초기화

```python
def ensure_configured():
    """DSPy LM 최초 1회 초기화 (싱글턴). 각 모듈이 import 시 호출."""
    global _configured
    if not _configured:
        lm = dspy.LM(f"openai/{EVAL_MODEL_NAME}", temperature=TEMPERATURE, cache=False)
        dspy.configure(lm=lm)
        _configured = True
```

`cache=False`: 상담은 매번 다른 응답이 필요하므로 캐시 비활성화.

### `bullet_module.py` — BulletDetect Signature

```python
class BulletDetect(dspy.Signature):
    """EFT 커플 상담 중 내담자 발화에서 방어적·공격적 발화(총알)를 감지한다."""
    reply: str    = dspy.InputField(desc="내담자 발화 텍스트")
    eft_stage: int = dspy.InputField(desc="현재 EFT 단계 (1/2/3)")
    is_female: bool = dspy.InputField(desc="True=여성 내담자, False=남성 내담자")

    bullet_detected: bool  = dspy.OutputField(desc="총알 감지 여부")
    bullet_type: str       = dspy.OutputField(desc="Reactive / Mistrust / None 중 하나만 출력")
    confidence: float      = dspy.OutputField(desc="감지 신뢰도 0.0~1.0")
    suggested_intervention: str = dspy.OutputField(desc="권장 개입 방식 한 문장 (한국어)")
```

`BulletDetector.forward()` 반환값: bullet_type은 `("Reactive", "Mistrust", "None")` 외의 값이면 `"None"`으로 보정. confidence는 0~1로 클램핑.

### `eval_module.py` — EFTEval Signature

```python
class EFTEval(dspy.Signature):
    """EFT 커플 상담 AI 응답의 품질을 6개 척도로 1~5점 채점한다."""
    # 입력: f_reply, m_reply, f_response, m_response, classification,
    #       f_attach_type, m_attach_type, eft_stage
    neutrality: float       = dspy.OutputField(desc="중립성 점수 1~5")
    validation_depth: float = dspy.OutputField(desc="타당화 깊이 점수 1~5")
    attach_coherence: float = dspy.OutputField(desc="애착 정합성 점수 1~5")
    cycle_reframing: float  = dspy.OutputField(desc="사이클 재구성 점수 1~5")
    actionability: float    = dspy.OutputField(desc="행동 가능성 점수 1~5")
    safety: float           = dspy.OutputField(desc="안전성 점수 1~5")
    improvement_hints: str  = dspy.OutputField(desc="미달 척도 개선 방향 한 문장")
```

모든 점수는 `max(1.0, min(5.0, float(v)))` 클램핑. 파싱 실패 시 기본값 3.0.

### `neutrality_module.py` — NeutralityCheck Signature

```python
class NeutralityCheck(dspy.Signature):
    """EFT 커플 상담 AI 응답이 여성 또는 남성 중 한쪽에 편향됐는지 검사한다."""
    # 입력: f_reply, m_reply, f_response, m_response, classification, eft_stage
    neutrality_score: float  = dspy.OutputField(desc="중립성 점수 1~5")
    bias_direction: str      = dspy.OutputField(desc="toward_f / toward_m / none")
    violations: str          = dspy.OutputField(desc="감지된 편향 항목들을 ;로 구분")
    reasoning: str           = dspy.OutputField(desc="판단 근거 한 문장 (한국어)")
    feedback_for_regen: str  = dspy.OutputField(desc="재생성 시 교정 지침")
```

`violations` 문자열은 `NeutralityJudge.forward()`에서 `;` 분리 → 리스트로 변환해 반환.

### Optimizer 연결 예시 (데이터 확보 후)

```python
# 학습 예시 데이터 준비
examples = [
    dspy.Example(
        f_reply="...", m_reply="...", f_response="...", m_response="...",
        classification="불안+거부회피", eft_stage=1,
    ).with_inputs("f_reply", "m_reply", "f_response", "m_response", "classification", "eft_stage")
]

# BootstrapFewShot: 좋은 예시를 자동으로 few-shot 프롬프트에 삽입
optimizer = dspy.BootstrapFewShot(metric=lambda ex, pred: pred.neutrality_score >= 4.0)
optimized = optimizer.compile(NeutralityJudge(), trainset=examples)
optimized.save("dspy_optimized/neutrality/v1.json")
```

### 버전 관리

```
dspy_optimized/
├── bullet/v1.json, v2.json, ...    ← 최신 버전 자동 로드
├── eval/v1.json, ...
└── neutrality/v1.json, ...
```

- 파일 없음 → 기존 프롬프트 사용 (현재 상태)
- v1.json 추가 → DSPy 최적화 자동 전환
- v2.json 추가 → 최신 버전 자동 로드 (v1 보존, 롤백 가능)
- 전부 삭제 → 기존 프롬프트로 즉시 복귀

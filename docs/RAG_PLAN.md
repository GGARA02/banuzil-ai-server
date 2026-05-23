# RAG 기능 구현 계획 — 동일 커플 과거 사이클 기반 검색

> 작성일: 2026-05-22
> 최종 수정: 2026-05-23
> 상태: **구현 완료** (코드 + DB 마이그레이션 적용 완료, 실동작 테스트 대기)

---

## 0. 구현 현황 (2026-05-23)

### 완료된 작업

**DB (Supabase 적용 완료)**
- `session_embeddings` 테이블 생성 (FK ON DELETE CASCADE)
- `mediation_sessions.rag_context` 컬럼 추가
- `match_best_couple_session` RPC 함수 생성
- pgvector 확장 활성화

**신규 코드**
- `services/rag/__init__.py`
- `services/rag/embedding_service.py` — 사이클 벡터화 + 보고서 전문 저장
- `services/rag/retrieval_service.py` — 동일 커플 사이클 유사도 검색
- `services/rag/rag_prompt_builder.py` — 검색 결과 → 프롬프트 텍스트
- `scripts/backfill_embeddings.py` — 기존 세션 일괄 임베딩
- `scripts/rag_migration.sql` / `scripts/rag_rollback.sql` — DB 마이그레이션/롤백

**기존 코드 수정**
- `config/settings.py` — `RAG_ENABLED`, `RAG_SIMILARITY_THRESHOLD` 추가
- `services/supabase_client.py` — `rpc()` 메서드 추가
- `config/prompts/eft_base.py` — `build_system_prompt(rag_context=...)` 주입
- `graphs/eft_graph.py` — State `rag_context` 필드 + create_initial_state + response_generator 전달
- `services/session_service.py` — ctx에 `rag_context` 추가
- `routers/counseling.py` — cycle 검색 연결, report 임베딩 저장 연결

### 남은 작업
- 실제 동작 테스트 (임베딩 저장 → 검색 → 프롬프트 주입 end-to-end)
- (선택) 기존 완료 세션 backfill 실행

---

## 1. 개요

### 1-1. 목적

같은 커플이 여러 차례 상담 세션을 진행할 때, **과거 세션의 사이클 정의**를 검색 키로 사용하여 가장 유사한 과거 세션을 찾고, 해당 세션의 **보고서 전문**을 현재 상담 프롬프트에 참고 컨텍스트로 주입한다.

### 1-2. 핵심 설계 원칙

| 항목 | 설계 |
|------|------|
| **임베딩 대상 (검색 키)** | 사이클 정의 텍스트 |
| **프롬프트 주입 내용** | 보고서 전문 (summary_text) |
| **검색 시점** | 사이클 정의 생성 직후 (1단계 → 2단계 전환 시) |
| **RAG 적용 구간** | 2단계부터 (1단계는 RAG 없이 진행) |

사이클 정의는 관계 패턴의 핵심을 3~5문장으로 농축한 텍스트이므로, 보고서 전문보다 유사도 매칭 정밀도가 높다. 검색 후 실제 프롬프트에 주입하는 건 풍부한 정보를 담은 보고서 전문.

### 1-3. 기대 효과

- 과거 세션에서 식별된 **반복 패턴**(사이클)과 유사한 갈등을 자동 감지
- 이전에 **효과적이었던 중재안/대화법**을 참고하여 2단계 이후 개입 품질 향상
- 같은 커플의 갈등 주제가 달라도 **패턴 구조가 유사하면** 과거 경험을 활용

### 1-4. 범위

- 검색 대상: **동일 커플의 과거 완료 세션** 중 사이클 정의가 존재하는 것만
- 검색 결과: 유사도 기준 이상(`RAG_SIMILARITY_THRESHOLD`)인 것 중 **최고 유사도 1건만** 반영
- 사이클 정의가 없는 과거 세션: 임베딩하지 않음 (패턴 파악이 안 된 세션은 참고 가치 낮음)

---

## 2. 시나리오

```
커플 (여성 user_id=5, 남성 user_id=7)

세션 #1 (3개월 전, 완료)
  사이클 정의: "여자친구가 불안한 마음에 연락을 자주 확인하고 빠른 답장을
    요구하면, 남자친구는 압박감을 느끼고 답장을 늦추거나 피하게 됩니다.
    남자친구가 멀어질수록 여자친구는 더 강하게 매달리거나 비난하게 되고,
    남자친구는 더 깊이 침묵하는 패턴이 반복됩니다."
  보고서: 비난자 연화 개입이 효과적이었음

세션 #2 (1개월 전, 완료)
  사이클 정의: "남자친구가 취업 스트레스로 감정을 숨기고 혼자 해결하려 하면,
    여자친구는 자신도 마음의 문을 닫게 됩니다. 서로를 배려하려는 선의가
    오히려 정서적 단절을 만들어내는 사이클에 갇혀 있습니다."
  보고서: 철회자 재관여 중심 접근

세션 #3 (오늘, 진행중)
  갈등: 가사분담 → "내가 다 하는데 아무 관심 없다"
  1단계 진행 → 사이클 정의 생성됨:
    "여자친구가 서운함을 비난의 형태로 전달하면, 남자친구는 방어적인 마음이
    들어 아무 말 없이 자리를 피합니다. 남자친구의 침묵을 여자친구는 무시로
    받아들여 더 크게 화를 내게 되고, 악순환이 이어집니다."

→ 사이클 정의 생성 직후 RAG 검색:
  세션 #1 사이클: 유사도 0.84 ← 기준(0.75) 이상, 최고값 → 채택
    (갈등 주제는 다르지만 "추격 → 철회 → 더 추격" 구조가 동일)
  세션 #2 사이클: 유사도 0.62 ← 기준 미만 → 탈락
    (상호 회피 구조로 패턴 자체가 다름)

→ 세션 #1의 보고서 전문이 2단계부터 시스템 프롬프트에 주입
```

---

## 3. DB 스키마

### 3-1. Supabase SQL

```sql
-- pgvector 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 세션 임베딩 테이블
CREATE TABLE session_embeddings (
  id              serial        PRIMARY KEY,
  session_id      int           NOT NULL UNIQUE
                                REFERENCES mediation_sessions(session_id),

  -- 동일 커플 필터용
  -- min(user_id_a, user_id_b)_max(...)  → 누가 initiator든 동일 키
  couple_key      varchar       NOT NULL,

  -- 세션 종료 시점 단계 (참고용)
  eft_final_stage smallint      DEFAULT 3,

  -- 임베딩 대상 원본 (사이클 정의)
  cycle_text      text          NOT NULL,

  -- 프롬프트 주입용 원본 (보고서 전문 + 사이클 정의)
  summary_text    text          NOT NULL,

  -- 벡터 (text-embedding-3-small = 1536차원, 사이클 정의 기반)
  embedding       vector(1536)  NOT NULL,

  created_at      timestamptz   DEFAULT now()
);

-- 인덱스
CREATE INDEX idx_session_embeddings_couple
  ON session_embeddings (couple_key);

CREATE INDEX idx_session_embeddings_vector
  ON session_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 10);
```

### 3-2. 벡터 검색 RPC 함수

유사도 기준 이상인 결과 중 최고 유사도 1건만 반환.

```sql
CREATE OR REPLACE FUNCTION match_best_couple_session(
  query_embedding       vector(1536),
  target_couple_key     varchar,
  exclude_session_id    int     DEFAULT 0,
  similarity_threshold  float   DEFAULT 0.75
)
RETURNS TABLE (
  session_id      int,
  cycle_text      text,
  summary_text    text,
  eft_final_stage smallint,
  similarity      float,
  created_at      timestamptz
)
LANGUAGE sql STABLE AS $$
  SELECT
    se.session_id,
    se.cycle_text,
    se.summary_text,
    se.eft_final_stage,
    1 - (se.embedding <=> query_embedding) AS similarity,
    se.created_at
  FROM session_embeddings se
  WHERE se.couple_key = target_couple_key
    AND se.session_id != exclude_session_id
    AND 1 - (se.embedding <=> query_embedding) >= similarity_threshold
  ORDER BY se.embedding <=> query_embedding
  LIMIT 1;
$$;
```

### 3-3. 기존 테이블 변경

없음. `session_embeddings` 신규 테이블만 추가.

---

## 4. 파일 구조

```
services/
  rag/
    __init__.py
    embedding_service.py      # 사이클 정의 벡터화 + 보고서 전문 저장
    retrieval_service.py      # 사이클 유사도 검색 + 캐시
    rag_prompt_builder.py     # 검색 결과(보고서) → 프롬프트 삽입 텍스트 변환

scripts/
  backfill_embeddings.py      # 기존 완료 세션 사이클 일괄 임베딩 스크립트
```

---

## 5. 모듈 상세 설계

### 5-1. `embedding_service.py` — 저장 파이프라인

#### couple_key 생성

```python
def make_couple_key(user_id_a: int, user_id_b: int) -> str:
    """두 유저 ID → 정렬된 커플 키 (누가 initiator든 동일)"""
    return f"{min(user_id_a, user_id_b)}_{max(user_id_a, user_id_b)}"
```

#### 임베딩 대상: 사이클 정의

사이클 정의 텍스트(3~5문장, ~100~200토큰)를 그대로 임베딩한다.
text-embedding-3-small 토큰 한도(8,191)에 비해 매우 짧으므로 truncation 불필요.

#### 프롬프트 주입용: 보고서 전문

검색 명중 시 GPT에 주입할 내용은 보고서 전문(8섹션 + 사이클 정의).

```python
def build_session_summary(
    cycle_definition: str,
    f_report: dict,
    m_report: dict,
) -> str:
    """보고서 전문 + 사이클 정의 이어붙이기. 프롬프트 주입용."""
    return f"""[사이클 정의]
{cycle_definition or '사이클 미정의'}

[여성 — 감정 정리]
{f_report['emotion_summary']}

[여성 — 파트너 이해]
{f_report['partner_understanding']}

[여성 — 중재안]
{f_report['mediation_plans']}

[여성 — 추천 대화법]
{f_report['recommended_dialogues']}

[남성 — 감정 정리]
{m_report['emotion_summary']}

[남성 — 파트너 이해]
{m_report['partner_understanding']}

[남성 — 중재안]
{m_report['mediation_plans']}

[남성 — 추천 대화법]
{m_report['recommended_dialogues']}"""
```

#### 임베딩 생성 + 저장

```python
from openai import AsyncOpenAI
from config.settings import EMBEDDING_MODEL

_openai = AsyncOpenAI()

async def embed_text(text: str) -> list[float]:
    """OpenAI text-embedding-3-small 호출"""
    resp = await _openai.embeddings.create(
        input=text,
        model=EMBEDDING_MODEL,
    )
    return resp.data[0].embedding


async def save_session_embedding(
    session_id: int,
    f_user_id: int,
    m_user_id: int,
    cycle_definition: str,
    f_report: dict,
    m_report: dict,
    eft_stage: int,
) -> None:
    """
    세션 완료 시 호출.
    사이클 정의가 없으면 임베딩하지 않는다.
    """
    if not cycle_definition or not cycle_definition.strip():
        logger.info(f"[{session_id}] 사이클 정의 없음 → 임베딩 스킵")
        return

    summary = build_session_summary(cycle_definition, f_report, m_report)
    vector  = await embed_text(cycle_definition)  # 사이클 정의만 벡터화

    await supa.insert("session_embeddings", {
        "session_id":      session_id,
        "couple_key":      make_couple_key(f_user_id, m_user_id),
        "eft_final_stage": eft_stage,
        "cycle_text":      cycle_definition,
        "summary_text":    summary,
        "embedding":       vector,     # 사이클 기반 벡터
    })
```

#### 호출 시점

`routers/counseling.py` POST `/ai/report` → `save_report()` 직후.
사이클 정의가 없으면 자동 스킵. 실패해도 보고서 응답에 영향 없음 (try-except).

---

### 5-2. `retrieval_service.py` — 검색

#### 검색 타이밍: 사이클 정의 생성 직후

기존 계획(라운드 1 첫 발화)에서 변경.
사이클 정의가 생성되는 시점 = 1단계 → 2단계 전환 시점.
2단계부터 본격적 개입이 시작되므로 과거 경험 참고가 가장 필요한 때.

#### 검색 전략: 사이클 vs 사이클

```
1. 현재 세션의 사이클 정의를 벡터화
2. couple_key 필터로 같은 커플만 대상
3. 유사도 >= RAG_SIMILARITY_THRESHOLD 인 것만
4. 그 중 유사도 최고 1건만 반환
5. 기준 미만이면 빈 결과 → RAG 미적용 (기존 동작)
```

```python
from services.rag.embedding_service import embed_text, make_couple_key
from config.settings import RAG_SIMILARITY_THRESHOLD

_rag_cache: dict[int, dict | None] = {}


async def find_best_past_session(
    f_user_id: int,
    m_user_id: int,
    current_session_id: int,
    cycle_definition: str,
) -> dict | None:
    """
    현재 사이클 정의와 가장 유사한 과거 세션 1건 반환.
    유사도가 기준 미만이면 None.
    """
    if current_session_id in _rag_cache:
        return _rag_cache[current_session_id]

    couple_key = make_couple_key(f_user_id, m_user_id)
    query_vec  = await embed_text(cycle_definition)

    results = await supa.rpc("match_best_couple_session", {
        "query_embedding":      query_vec,
        "target_couple_key":    couple_key,
        "exclude_session_id":   current_session_id,
        "similarity_threshold": RAG_SIMILARITY_THRESHOLD,
    })

    best = results[0] if results else None

    _rag_cache[current_session_id] = best
    return best
```

#### 호출 시점

POST `/ai/cycle` → 사이클 정의 생성 직후.
결과는 `mediation_sessions`에 `rag_context`로 저장하여 이후 라운드에서 재사용.

---

### 5-3. `rag_prompt_builder.py` — 프롬프트 변환

```python
def build_rag_context(best_session: dict | None) -> str:
    """검색된 최고 유사도 과거 세션 1건의 보고서 → 프롬프트 삽입 텍스트"""
    if not best_session:
        return ""

    similarity = best_session.get("similarity", 0)
    text       = best_session["summary_text"]  # 보고서 전문

    return (
        f"\n\n[이 커플의 과거 상담 기록 — 유사도 {similarity:.0%} — 내부 참고용]\n"
        f"{text}\n\n"
        "[과거 기록 활용 규칙]\n"
        "- 과거 세션에서 반복된 패턴(사이클)과 효과적이었던 접근 방식을 참고하라.\n"
        "- 과거 상담 내용을 내담자에게 직접 인용하거나 언급하지 마라.\n"
        "- 현재 내담자의 실제 발화와 현재 갈등 상황을 항상 최우선으로 반영하라.\n"
    )
```

---

## 6. 기존 코드 수정 지점

### 6-1. `config/settings.py` — RAG 설정값 추가

```python
# ── RAG ───────────────────────────────────────────────────
RAG_ENABLED:               bool  = os.getenv("RAG_ENABLED", "true").lower() == "true"
RAG_SIMILARITY_THRESHOLD:  float = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.75"))
EMBEDDING_MODEL:           str   = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")  # 기존
PGVECTOR_URL:              str   = os.getenv("PGVECTOR_URL", "...")                         # 기존
```

### 6-2. `routers/counseling.py` — 2곳 수정

#### (A) POST `/ai/cycle` — 사이클 정의 생성 후 RAG 검색

사이클 정의가 생성되면 즉시 과거 사이클 검색 실행.
검색 결과(rag_context)를 `mediation_sessions`에 저장하여 이후 라운드에서 사용.

```python
# 사이클 정의 생성 후
await save_cycle_definition(req.session_id, definition)

# 신규: RAG 검색
rag_context = ""
if RAG_ENABLED:
    try:
        from services.rag.retrieval_service import find_best_past_session
        from services.rag.rag_prompt_builder import build_rag_context
        best = await find_best_past_session(
            f_user_id=ctx["f_user_id"],
            m_user_id=ctx["m_user_id"],
            current_session_id=req.session_id,
            cycle_definition=definition,
        )
        rag_context = build_rag_context(best)
        if rag_context:
            await supa.update(
                "mediation_sessions",
                {"session_id": f"eq.{req.session_id}"},
                {"rag_context": rag_context},
            )
    except Exception as e:
        logger.warning(f"[{req.session_id}] RAG 검색 실패 (무시): {e}")
```

#### (B) POST `/ai/report` — 보고서 생성 후 임베딩 저장

```python
# 신규: 세션 임베딩 저장 (사이클 정의가 있을 때만)
if RAG_ENABLED:
    try:
        from services.rag.embedding_service import save_session_embedding
        await save_session_embedding(
            session_id=req.session_id,
            f_user_id=ctx["f_user_id"],
            m_user_id=ctx["m_user_id"],
            cycle_definition=ctx["cycle_definition"],
            f_report=f_sections,
            m_report=m_sections,
            eft_stage=ctx["eft_stage"],
        )
    except Exception as e:
        logger.warning(f"[{req.session_id}] 임베딩 저장 실패 (무시): {e}")
```

### 6-3. `mediation_sessions` 테이블 — 컬럼 추가

```sql
ALTER TABLE mediation_sessions ADD COLUMN rag_context text DEFAULT '';
```

RAG 검색 결과를 세션에 저장. 이후 라운드에서 fetch_session_context()로 읽어옴.

### 6-4. `services/session_service.py` — ctx에 rag_context 추가

`fetch_session_context()` return dict에 추가:

```python
return {
    ...
    "rag_context": session.get("rag_context", ""),
}
```

별도 검색 로직 불필요. 이미 사이클 정의 시점에 검색 완료 후 DB에 저장돼 있음.

### 6-5. `graphs/eft_graph.py` — State + 전달

```python
class EFTState(TypedDict):
    ...
    rag_context:         str           # 과거 세션 참고 컨텍스트 (신규)
```

`create_initial_state()`에 `rag_context` 파라미터 추가.
`node_response_generator`에서 `build_system_prompt()` 호출 시 전달.

### 6-6. `config/prompts/eft_base.py` — 시스템 프롬프트 주입

`build_system_prompt()`에 `rag_context: str = ""` 파라미터 추가.

```python
rag_section = ""
if rag_context:
    rag_section = f"\n\n{rag_context}"
```

---

## 7. 토큰 예산

| 컴포넌트 | 기존 (토큰) | RAG 추가 후 |
|---|---|---|
| System Prompt 본문 | ~2,500 | ~2,500 |
| 애착 분류 섹션 | ~300~800 | 그대로 |
| Stage Instruction | ~200~400 | 그대로 |
| **RAG Context (보고서 1건 전문)** | **0** | **~800~1,500** |
| User Message | ~300~500 | 그대로 |
| History (5라운드) | ~2,000~3,000 | 그대로 |
| **합계** | **~5,500~7,000** | **~6,300~8,500** |

GPT-4o 128k 컨텍스트 대비 여유 충분. 유사도 미달 시 0토큰.

---

## 8. 데이터 흐름도

### 저장 흐름 (세션 종료 시)

```
POST /ai/report
  │
  ├─ generate_report()        → f_sections, m_sections
  ├─ save_report()            → mediation_reports INSERT
  └─ save_session_embedding() → session_embeddings INSERT (사이클 있을 때만)
       │
       ├─ cycle_definition 존재 확인 (없으면 스킵)
       ├─ embed_text(cycle_definition)  → 사이클 기반 1536차원 벡터
       ├─ build_session_summary()       → 보고서 전문 이어붙이기
       └─ supa.insert()
            ├─ embedding   = 사이클 벡터 (검색용)
            ├─ cycle_text  = 사이클 원문 (디버깅용)
            └─ summary_text = 보고서 전문 (프롬프트 주입용)
```

### 검색 흐름 (사이클 정의 생성 직후)

```
POST /ai/cycle (정의 생성 모드)
  │
  ├─ 사이클 정의 텍스트 생성
  ├─ save_cycle_definition()  → mediation_sessions UPDATE
  │
  └─ RAG 검색 (신규)
       │
       ├─ embed_text(현재 사이클 정의) → 쿼리 벡터
       ├─ match_best_couple_session RPC 호출
       │    ├─ couple_key 필터: 같은 커플만
       │    ├─ similarity >= threshold (기본 0.75)
       │    └─ ORDER BY similarity DESC LIMIT 1
       │
       ├─ 기준 이상 1건 있음
       │    → build_rag_context(best) → rag_context 생성
       │    → mediation_sessions UPDATE (rag_context 저장)
       │    → 2단계부터 시스템 프롬프트에 포함
       │
       └─ 기준 이상 없음
            → rag_context = "" (RAG 미적용, 기존 동작)
```

### 2단계 이후 라운드 진행 시

```
POST /ai/round-analyze
  │
  ├─ fetch_session_context()
  │    └─ session.rag_context 읽기 (이미 DB에 저장돼 있음)
  │
  ├─ create_initial_state(rag_context=ctx["rag_context"])
  │
  └─ LangGraph 실행
       └─ node_response_generator
            └─ build_system_prompt(rag_context=...)
                 → 과거 보고서 전문이 시스템 프롬프트에 포함
```

---

## 9. 구현 순서

| # | 작업 | 수정 파일 | 상태 |
|---|---|---|---|
| 1 | Supabase SQL 실행 (session_embeddings 테이블 + RPC 함수) | Supabase Dashboard | ✅ |
| 2 | mediation_sessions에 rag_context 컬럼 추가 | Supabase Dashboard | ✅ |
| 3 | `config/settings.py` RAG 설정값 추가 | settings.py | ✅ |
| 4 | `services/rag/__init__.py` 생성 | 신규 | ✅ |
| 5 | `services/rag/embedding_service.py` 구현 | 신규 | ✅ |
| 6 | `services/rag/rag_prompt_builder.py` 구현 | 신규 | ✅ |
| 7 | `services/rag/retrieval_service.py` 구현 | 신규 | ✅ |
| 8 | `services/supabase_client.py`에 `rpc()` 메서드 추가 | 기존 수정 | ✅ |
| 9 | `routers/counseling.py` — cycle 엔드포인트에 RAG 검색 연결 | 기존 수정 | ✅ |
| 10 | `routers/counseling.py` — report 엔드포인트에 임베딩 저장 연결 | 기존 수정 | ✅ |
| 11 | `services/session_service.py` — ctx에 rag_context 추가 | 기존 수정 | ✅ |
| 12 | `graphs/eft_graph.py` — State + create_initial_state + response_generator | 기존 수정 | ✅ |
| 13 | `config/prompts/eft_base.py` — 프롬프트 주입 | 기존 수정 | ✅ |
| 14 | `scripts/backfill_embeddings.py` — 기존 세션 백필 | 신규 | ✅ |
| 15 | `.env` 설정값 추가 + 통합 테스트 | .env | ⬜ 대기 |

---

## 10. 설정값 요약

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `RAG_ENABLED` | `true` | RAG 기능 전체 ON/OFF |
| `RAG_SIMILARITY_THRESHOLD` | `0.75` | 이 값 이상인 세션만 채택 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 임베딩 모델 (기존) |

---

## 11. 엣지 케이스

| 상황 | 처리 |
|---|---|
| 첫 세션 (과거 기록 없음) | RPC 결과 0건 → `rag_context = ""` → 기존과 동일 동작 |
| 과거 세션 있으나 전부 유사도 미달 | RPC 결과 0건 → 동일하게 미적용 |
| 과거 세션에 사이클 정의가 없었음 | 애초에 임베딩 안 됨 → 검색 대상에서 자동 제외 |
| 현재 세션에 사이클 정의 없이 종료 | 임베딩 저장 스킵 (save_session_embedding에서 early return) |
| 임베딩 API 실패 (저장 시) | try-except 무시, 보고서는 정상 반환 |
| 임베딩 API 실패 (검색 시) | try-except 무시, `rag_context = ""` |
| `RAG_ENABLED=false` | 저장/검색 로직 전부 스킵 |
| 같은 커플이 동시에 2개 세션 진행 | `exclude_session_id`로 현재 세션 제외 |

---

## 12. 기존 계획 대비 변경 요약

| 항목 | v1 (기존) | v2 (현재) | 변경 이유 |
|---|---|---|---|
| 임베딩 대상 | 보고서 전문 (~1500토큰) | 사이클 정의 (~100~200토큰) | 패턴 핵심만 담아 유사도 정밀도 향상 |
| 검색 쿼리 | 라운드 1 첫 발화 | 현재 사이클 정의 | 사이클 vs 사이클 동질 비교 |
| 검색 시점 | 라운드 1 시작 시 | 사이클 정의 생성 직후 | 2단계 개입에 맞춘 적시 타이밍 |
| RAG 적용 구간 | 세션 전체 | 2단계부터 | 1단계는 선입견 없이 진행 |
| 사이클 없는 세션 | fallback으로 보고서 임베딩 | 임베딩 안 함 | 패턴 파악 안 된 세션은 참고 가치 낮음 |
| RAG 결과 저장 | 세션 내 캐시만 | DB에 rag_context 저장 | 서버 재시작해도 유지, 매 라운드 재검색 불필요 |

---

## 13. 추후 확장 가능성

- **사이클 정의 시점 재검색**: 사이클이 수정되면 rag_context도 재검색
- **유사도 threshold 자동 조정**: 과거 세션 수에 따라 threshold 동적 변경
- **타 커플 참조 확장**: 동의 기반으로 유사 유형 커플 데이터 참조 (현재 범위 밖)
- **다건 참조**: 유사도 상위 N건의 보고서를 요약하여 주입 (현재는 1건)

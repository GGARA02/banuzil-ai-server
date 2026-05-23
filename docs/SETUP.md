# 바느질 AI 서버 — 설치 및 실행 가이드

## GitHub에서 받은 후 따로 받아야 할 파일

GitHub에는 용량 문제로 아래 파일들이 올라가 있지 않다. **별도로 전달받아서** 직접 넣어야 한다.

### 1. KcELECTRA 파인튜닝 모델 (필수)

| 파일 | 넣을 위치 |
|------|----------|
| `best_model.pt` | `models/concat_unweight/best_model.pt` |

> `models/concat_unweight/` 폴더는 이미 GitHub에 있음 (config.json, tokenizer.json, model_meta.json 등 포함).
> **pt 파일만 추가로 넣으면 된다.**

### 2. 환경 변수 파일 (필수)

`.env` 파일은 레포에 포함되어 있다. 아래 값들을 본인 환경에 맞게 교체한다.

```
OPENAI_API_KEY=sk-...                          ← OpenAI API 키
SUPABASE_URL=https://xxxx.supabase.co          ← Supabase 프로젝트 URL
SUPABASE_KEY=sb_secret_...                     ← Supabase secret 키
```

---

## 설치

### Python 버전

Python 3.10 이상 권장.

### 패키지 설치

```bash
python -m pip install -r requirements.txt
```

PyTorch는 GPU 환경이면 [pytorch.org](https://pytorch.org/get-started/locally/)에서 CUDA 버전 별도 설치 권장.
CPU만 있어도 동작하지만 감성 분석 속도가 느려진다.

---

## 서버 실행

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

정상 실행 시 출력:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

---

## 테스트

### Swagger UI

API 명세 및 직접 호출 테스트:

```
http://localhost:8000/docs
```

### 헬스체크

**감성 분석 모델 로드 확인:**

```bash
curl http://localhost:8000/emotion/health
```

정상 응답:

```json
{
  "status": "ok",
  "model": "concat_unweight (hierarchical)"
}
```

**Supabase 연결 확인:**

```bash
curl http://localhost:8000/health/supabase
```

정상 응답:

```json
{
  "supabase": "connected"
}
```

---

## 폴더 구조 최종 확인

서버 실행 전 아래 파일들이 모두 있는지 확인:

```
banuzil-ai-server/
├── .env                                    ✅ 레포에 포함 — API 키 교체 필요
├── models/
│   └── concat_unweight/
│       ├── best_model.pt                   ✅ 별도 전달
│       ├── config.json                     (GitHub에 있음)
│       ├── tokenizer.json                  (GitHub에 있음)
│       └── model_meta.json                 (GitHub에 있음)
└── requirements.txt                        (GitHub에 있음)
```

---

## Supabase 연동

AI 서버는 Supabase REST API(PostgREST)를 통해 DB와 통신한다.
별도 패키지 불필요 — httpx로 직접 호출.

### 연결 구조

```
AI 서버 (FastAPI)
    │  HTTP + apikey 헤더
    ▼
https://xxxx.supabase.co/rest/v1/테이블명
    │
    ▼
PostgreSQL DB
```

### 사용 예시

```python
from services.supabase_client import supa

# 단건 조회
users = await supa.get("users", {"user_id": "eq.abc123"})

# 삽입
await supa.insert("mediation_sessions", {"session_id": "xxx", "eft_stage": 1})

# 업데이트
await supa.update("mediation_sessions", {"session_id": "eq.xxx"}, {"eft_stage": 2})
```

### DB 테이블 목록

| 테이블 | 설명 |
|--------|------|
| `users` | 사용자 정보 (mbti, gender, anxiety 등) |
| `mediation_sessions` | 상담 세션 상태 |
| `mediation_records` | 라운드별 대화 기록 |
| `mediation_reports` | 최종 보고서 (4섹션 × 2명) |
| `friendships` | 커플 연결 정보 |
| `user_attachments` | 애착 유형 점수 |
| `session_embeddings` | RAG용 사이클 임베딩 (pgvector) |

### RAG 기능 사용 시 (선택)

동일 커플 과거 세션 참조(RAG)를 쓰려면 Supabase SQL Editor에서 `scripts/rag_migration.sql`을
1회 실행해야 한다 (pgvector 확장 + `session_embeddings` 테이블 + `rag_context` 컬럼 + 검색 RPC).
`.env`에서 `RAG_ENABLED=false`로 끄면 마이그레이션 없이도 기존 상담은 정상 동작한다.

```
RAG_ENABLED=true                  ← RAG 기능 ON/OFF (기본 true)
RAG_SIMILARITY_THRESHOLD=0.55     ← 유사도 채택 기준 (한국어 임베딩 실측 튜닝값)
EMBEDDING_MODEL=text-embedding-3-small
```

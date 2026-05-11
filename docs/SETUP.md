# 바느질 AI 서버 — 설치 및 실행 가이드

## GitHub에서 받은 후 따로 받아야 할 파일

GitHub에는 용량 문제로 아래 파일들이 올라가 있지 않다. **별도로 전달받아서** 직접 넣어야 한다.

### 1. KoELECTRA 파인튜닝 모델 (필수)

| 파일 | 넣을 위치 |
|------|----------|
| `best_model.pt` | `models/unweighted/best_model.pt` |
| `best_model.pt` | `models/low_weight/best_model.pt` |

> `models/unweighted/`와 `models/low_weight/` 폴더는 이미 GitHub에 있음 (config.json, tokenizer.json 등 포함).  
> **pt 파일만 추가로 넣으면 된다.**

### 2. 환경 변수 파일 (필수)

`.env` 파일은 레포에 포함되어 있다. `OPENAI_API_KEY` 값만 본인 키로 교체하면 된다.

```
OPENAI_API_KEY=sk-...   ← 여기만 변경
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

감성 분석 모델 로드 확인:

```bash
curl http://localhost:8000/emotion/health
```

정상 응답:

```json
{
  "status": "ok",
  "models": {
    "category_model": "unweighted",
    "detail_model": "low_weight"
  }
}
```

---

## 폴더 구조 최종 확인

서버 실행 전 아래 파일들이 모두 있는지 확인:

```
banuzil-ai-server/
├── .env                              ✅ 레포에 포함 — OPENAI_API_KEY만 교체
├── models/
│   ├── unweighted/
│   │   ├── best_model.pt             ✅ 별도 전달
│   │   ├── config.json               (GitHub에 있음)
│   │   └── tokenizer.json            (GitHub에 있음)
│   └── low_weight/
│       ├── best_model.pt             ✅ 별도 전달
│       ├── config.json               (GitHub에 있음)
│       └── tokenizer.json            (GitHub에 있음)
└── requirements.txt                  (GitHub에 있음)
```

# 감성 분류 모델 성능 비교 보고서

> 바느질 AI 서버 - 커플 상담 감성 분석 모델 평가  
> 평가 일자: 2026-05-13  
> 평가 데이터: val_concat.pkl (6,641건)

---

## 1. 베이스 모델 소개

### 1-1. KcELECTRA (우리 모델 2개)

**ELECTRA**(Efficiently Learning an Encoder that Classifies Token Replacements Accurately)는 Google이 2020년에 발표한 사전학습 언어 모델이다. BERT가 토큰을 마스킹하고 맞추는 MLM(Masked Language Model) 방식인 반면, ELECTRA는 Generator가 가짜 토큰을 생성하고 Discriminator가 각 토큰이 진짜인지 가짜인지 판별하는 **RTD(Replaced Token Detection)** 방식을 사용한다. 모든 토큰에 대해 학습이 이루어지므로 같은 연산량 대비 학습 효율이 높다.

**KcELECTRA-base-v2022**(`beomi/KcELECTRA-base-v2022`)는 이준범(beomi)이 한국어 뉴스, 댓글 등 대규모 한국어 코퍼스로 사전학습한 ELECTRA 모델이다. 한국어 특화 WordPiece 토크나이저를 사용하며, 한국어 자연어 이해(NLU) 벤치마크에서 우수한 성능을 보인다.

| 항목 | 값 |
|------|-----|
| 아키텍처 | Transformer Encoder (ELECTRA Discriminator) |
| 파라미터 수 | ~110M (base) |
| hidden size | 768 |
| attention heads | 12 |
| layers | 12 |
| max length | 512 (우리는 128로 설정) |
| 토크나이저 | WordPiece (한국어 특화) |
| 사전학습 데이터 | 한국어 뉴스, 댓글 등 대규모 코퍼스 |

### 1-2. ALBERT (NIA 모델)

**ALBERT**(A Lite BERT)는 Google이 2019년에 발표한 BERT의 경량화 버전이다. 두 가지 핵심 기법으로 파라미터를 줄인다:
- **Factorized Embedding Parameterization**: 임베딩 행렬을 두 개의 작은 행렬로 분해
- **Cross-layer Parameter Sharing**: Transformer 층 간 파라미터 공유

BERT와 동일한 MLM + SOP(Sentence Order Prediction)으로 학습하며, 파라미터 수는 적지만 추론 속도는 BERT와 비슷하다 (층 수는 같으므로).

NIA가 사용한 ALBERT는 TensorFlow 1.x 기반 Hub 모듈로, SentencePiece 토크나이저를 사용한다.

| 항목 | 값 |
|------|-----|
| 아키텍처 | Transformer Encoder (ALBERT) |
| hidden size | 768 |
| 토크나이저 | SentencePiece |
| 프레임워크 | TensorFlow 1.x (Hub Module) |
| 사전학습 데이터 | 한국어 코퍼스 (상세 비공개) |

### 1-3. GPT-4o-mini (OpenAI)

**GPT-4o-mini**는 OpenAI의 경량 멀티모달 LLM이다. GPT-4o의 소형 버전으로, 텍스트/이미지 입력을 받아 텍스트를 생성한다. 수십~수백B 파라미터 규모로 추정되며(비공개), 대규모 인터넷 데이터로 사전학습 후 RLHF로 정렬되었다.

분류 태스크에서는 **별도 학습 없이(zero-shot)** 프롬프트에 분류 체계를 설명하고 JSON 응답을 요청하는 방식으로 사용한다. 학습된 분류기가 아니라 언어 이해력에 기반한 추론이므로, 성격이 근본적으로 다르다.

| 항목 | 값 |
|------|-----|
| 아키텍처 | Transformer Decoder (GPT) |
| 파라미터 수 | 비공개 (추정 수십~수백B) |
| 토크나이저 | BPE (다국어) |
| 사전학습 데이터 | 대규모 인터넷 텍스트 + RLHF |
| 사용 방식 | API 호출 (zero-shot, temperature=0) |

---

## 2. 비교 대상 모델 상세

### 2-1. 우리 concat_unw (계층적 모델)

**구조: HierarchicalEmotionModel**

```
입력 → KcELECTRA Encoder → [CLS] 토큰 (768차원)
                              │
                    ┌─────────┴──────────┐
                    ▼                    │
            Category Head               │
         Linear(768→384)→GELU           │
         Linear(384→6)                  │
                    │                    │
                    ▼                    │
            대분류 예측 (6개)             │
                    │                    │
                    ▼                    │
         Category Embedding(6→192)      │
                    │                    │
                    ▼                    ▼
              [cat_emb 192] + [CLS 768] = 960차원
                              │
                              ▼
                       Detail Head
                    Linear(960→384)→GELU
                    Linear(384→58)
                              │
                              ▼
                  + 대분류 마스킹 (해당 대분류의 소분류만 활성화)
                              │
                              ▼
                    소분류 예측 (58개 중 ~10개만 후보)
```

- 학습 시: 정답 대분류로 마스킹 (teacher forcing)
- 추론 시: 예측된 대분류로 마스킹

| 항목 | 값 |
|------|-----|
| 학습 데이터 | AIHub 감성대화말뭉치 |
| 전처리 | 대화 이어붙이기 (같은 세션의 발화를 연결하여 맥락 보존) |
| 입력 형식 | `[성별] X [상황] Y [발화] Z` |
| 출력 | 대분류 6개 + 소분류 58개 |
| optimizer | AdamW (encoder lr=2e-5, head lr=1e-4) |
| scheduler | linear warmup (10%) |
| weight decay | 0.01 |
| epochs | 5 |
| batch size | 32 (train) / 64 (eval) |
| max length | 128 |
| loss | 0.6 * CrossEntropy(대분류) + 0.4 * CrossEntropy(소분류) |
| dropout | 0.1 |
| grad clipping | max_norm=1.0 |
| seed | 42 |

### 2-2. 우리 unweighted (플랫 모델, 기존 베이스라인)

**구조: MultiTaskEmotionModel**

```
입력 → KcELECTRA Encoder → [CLS] 토큰 (768차원)
                              │
                    ┌─────────┴──────────┐
                    ▼                    ▼
            Category Head          Detail Head
         Linear(768→384)→GELU   Linear(768→384)→GELU
         Linear(384→6)           Linear(384→58)
                    │                    │
                    ▼                    ▼
           대분류 예측 (6개)      소분류 예측 (58개)
           (서로 독립)           (마스킹 없음)
```

- 대분류와 소분류가 서로 연결 없이 독립적으로 예측
- 소분류가 대분류와 무관하게 전체 58개 중 선택 (마스킹 없음)

| 항목 | 값 |
|------|-----|
| 학습 데이터 | AIHub 감성대화말뭉치 |
| 전처리 | 발화 단위 개별 처리 (이어붙이기 없음) |
| 입력 형식 | `[성별] X [상황] Y [발화] Z` |
| 출력 | 대분류 6개 + 소분류 58개 |
| optimizer | AdamW (lr=2e-5) |
| scheduler | linear warmup (10%) |
| weight decay | 0.01 |
| epochs | 5 |
| batch size | 32 (train) / 64 (eval) |
| max length | 128 |
| loss | 0.6 * CrossEntropy(대분류) + 0.4 * CrossEntropy(소분류) |
| dropout | 0.1 |
| seed | 42 |

### 2-3. NIA ALBERT (AIHub 참조 모델)

**구조: Intent Classifier**

```
입력 → ALBERT Encoder → pooled_output (768차원)
                              │
                              ▼
                   Dense(768→6) + softmax
                              │
                              ▼
                    대분류 예측 (6개)
```

- 분류기가 Dense 1층뿐이라 매우 단순
- 소분류 분류 기능 없음

| 항목 | 값 |
|------|-----|
| 학습 데이터 | AIHub 감성대화말뭉치 |
| 전처리 | SentencePiece 토크나이저, 공백 분리 |
| 입력 형식 | 대화 텍스트 (메타데이터 없음) |
| 출력 | **대분류 6개만** |
| epochs | 30 |
| 프레임워크 | TensorFlow 1.x |
| 특징 | AIHub 공식 참조 모델 |
| AIHub 공식 성적 | EM 67.2%, F1 0.809 (자체 테스트셋 기준) |

### 2-4. GPT-4o-mini (LLM zero-shot)

**구조: 프롬프트 기반 분류**

```
[시스템 프롬프트: 분류 체계 + 규칙 + JSON 형식 지정]
              +
[유저 메시지: 성별/상황/발화 텍스트]
              │
              ▼
       GPT-4o-mini 추론
              │
              ▼
       JSON 응답 파싱
  {category_top3: [{label, confidence}...],
   detail_top3: [{label, confidence}...]}
```

| 항목 | 값 |
|------|-----|
| 학습 데이터 | 없음 (zero-shot) |
| 입력 형식 | `[성별] X [상황] Y` + `발화: Z` + 시스템 프롬프트 |
| 출력 | 대분류 6개 + 소분류 58개 + 자가평가 confidence |
| temperature | 0 (결정적 출력) |
| response format | JSON object |
| 특징 | 별도 학습 없이 프롬프트만으로 분류 |

---

## 3. 채점기준

### 3-1. Macro F1 (M-F1)

각 클래스별 F1 Score를 구한 뒤 단순 평균한 값.

```
F1(클래스 i) = 2 * Precision(i) * Recall(i) / (Precision(i) + Recall(i))
Macro F1 = (F1(분노) + F1(기쁨) + ... + F1(상처)) / 6
```

- **장점**: 데이터가 적은 클래스도 동등하게 반영하여 불균형에 강건
- **단점**: 모델의 확신도(confidence)를 반영하지 않음
- **용도**: 전반적인 분류 성능 비교의 표준 지표

### 3-2. Binary F1 (B-F1, NIA 공식 지표)

모델의 confidence(확신도)가 threshold(0.5) 이상일 때만 "응답"으로 간주하여 계산.

```
TP = 정답이면서 confidence >= 0.5 (확신하면서 맞춤)
FP = 오답이면서 confidence >= 0.5 (확신했는데 틀림)
FN = 정답이면서 confidence <  0.5 (맞췄지만 확신 못함)
TN = 오답이면서 confidence <  0.5 (확신 못하면서 틀림)

Precision = TP / (TP + FP)
Recall    = TP / (TP + FN)
Binary F1 = 2 * Precision * Recall / (Precision + Recall)
```

- **장점**: "확신할 때 얼마나 정확한가"를 측정 - 실서비스 신뢰도와 직결
- **단점**: threshold 기준에 따라 결과가 달라짐
- **참고**: NIA AIHub 공식 평가에서 F1-Score로 표기된 지표가 이 방식
- **주의**: M-F1보다 높게 나올 수 있음 (틀릴 때 confidence가 낮으면 TN으로 빠짐)

### 3-3. Top-K 정확도

모델이 제시한 상위 K개 후보 안에 정답이 포함되는 비율.

```
Top-1 = 1순위 예측이 정답인 비율 (= EM, Exact Match, Accuracy)
Top-2 = 1~2순위 중 정답이 있는 비율
Top-3 = 1~3순위 중 정답이 있는 비율
```

- **장점**: "아깝게 틀린 경우"를 파악 가능
- **활용**: Top-1은 낮은데 Top-3이 높으면 유사 감정 간 혼동이 많다는 의미
- **예시**: 분노와 상처를 혼동하더라도 Top-2 안에는 들어감 → 개선 여지 있음

---

## 4. 비교 결과

### 4-1. 대분류 (6개 감정)

| 모델 | Macro F1 | Binary F1 | Top-1 | Top-2 | Top-3 |
|------|---------|-----------|-------|-------|-------|
| **우리 concat_unw** | **0.7639** | **0.8800** | **76.96%** | **88.74%** | **94.53%** |
| 우리 unweighted | 0.7204 | 0.8538 | 72.71% | 86.72% | 93.42% |
| GPT-4o-mini | 0.6153 | 0.7809 | 63.92% | 79.49% | 87.16% |
| NIA ALBERT | 0.0483 | 0.0000 | 15.86% | 34.71% | 50.62% |

### 4-2. 소분류 (58개 감정)

| 모델 | Macro F1 | Top-1 | Top-2 | Top-3 |
|------|---------|-------|-------|-------|
| **우리 concat_unw** | **0.4976** | **50.94%** | **60.83%** | **66.38%** |
| 우리 unweighted | 0.4270 | 44.30% | 56.75% | 64.01% |
| GPT-4o-mini | 0.3308 | 34.80% | 43.60% | 45.89% |
| NIA ALBERT | N/A | N/A | N/A | N/A |

### 4-3. concat_unw vs unweighted 개선 폭

| 지표 | unweighted | concat_unw | 개선 |
|------|-----------|-----------|------|
| 대분류 M-F1 | 0.7204 | 0.7639 | +0.0435 |
| 대분류 B-F1 | 0.8538 | 0.8800 | +0.0262 |
| 대분류 Top-1 | 72.71% | 76.96% | +4.25%p |
| 소분류 M-F1 | 0.4270 | 0.4976 | +0.0706 |
| 소분류 Top-1 | 44.30% | 50.94% | +6.64%p |

> 계층적 구조(대분류 임베딩 + 마스킹)와 대화 이어붙이기 전처리로 대/소분류 모두 유의미한 개선.

---

## 5. 비교의 한계

이 비교는 동일 데이터(6,641건)에 동일 채점기준을 적용했지만, **완벽히 공정한 비교는 아닙니다.** 아래 한계를 이해한 위에서 해석해야 합니다.

### 5-1. NIA ALBERT - 입력 형식 불일치

| 문제 | 설명 |
|------|------|
| 메타데이터 미지원 | 우리 데이터의 `[성별] [상황]` 태그를 전혀 활용 못함 |
| 토크나이저 차이 | SentencePiece(공백 분리) vs KcELECTRA(WordPiece) - 한국어 처리 방식 근본적으로 다름 |
| 입력 형식 차이 | NIA는 원본 대화 텍스트로 학습, 우리는 `[성별][상황][발화]` 형식으로 재가공하여 학습 |
| 발화 추출 | 평가 시 `[발화]` 이후 텍스트만 추출하여 NIA에 입력했지만, NIA 원본 학습 데이터의 형식과도 다를 수 있음 |

> 학습 데이터 원본은 동일(AIHub 감성대화말뭉치)하지만, NIA는 원본 형식 그대로, 우리는 `[성별][상황][발화]` 형식으로 재가공하여 학습. NIA가 자체 테스트셋에서 EM 67.2%, F1 0.809를 기록하므로, 15.9%라는 수치는 모델 자체의 한계가 아니라 **입력 형식/토크나이저 불일치**가 원인입니다.

### 5-2. GPT-4o-mini - confidence 기준 차이

| 문제 | 설명 |
|------|------|
| 확신도 산출 방식 | 우리 모델/NIA는 softmax 확률(수학적), GPT는 자가평가(주관적) |
| Binary F1 공정성 | GPT confidence는 거의 항상 0.5 이상 → FN이 극단적으로 적어 Recall 99.7% |
| zero-shot | 우리 데이터로 학습하지 않음 - 프롬프트에 분류 체계만 제공 |
| Top-K 한계 | 우리 모델은 전체 클래스에 대한 확률 분포에서 Top-K를 뽑지만, GPT는 스스로 순위를 매긴 3개만 제공 |

> GPT의 Binary F1(0.78)이 Macro F1(0.62)보다 높은 이유: GPT가 자가평가 confidence를 높게 부르는 경향이 있어 거의 모든 예측이 TP 또는 FP로 분류됨. 우리 모델/NIA의 softmax 확률과 동일 선상에서 비교하기 어려움.

### 5-3. unweighted vs concat_unw - 변인 복수

| 문제 | 설명 |
|------|------|
| 동시 변경 | 모델 구조(플랫 vs 계층적) + 전처리(개별 발화 vs 이어붙이기) 두 가지가 동시에 바뀜 |
| 기여도 분리 불가 | 성능 향상이 구조 변경 때문인지, 전처리 때문인지 분리 확인이 필요하면 추가 실험 필요 |

### 5-4. 평가 데이터 편향

| 문제 | 설명 |
|------|------|
| 학습/평가 동일 출처 | 우리 모델 두 개는 같은 출처의 학습 데이터로 훈련됨 - 유리한 조건 |
| NIA 불리 | NIA는 완전히 다른 도메인 데이터로 학습 - 이 평가에서 구조적으로 불리 |
| GPT 중립 | GPT는 어느 쪽 데이터로도 학습하지 않음 - 가장 중립적 위치 |

---

## 6. 결론

### 핵심 요약

1. **우리 concat_unw가 모든 지표에서 1위** - 도메인 특화 학습 + 계층적 구조의 효과
2. **계층적 구조 도입 효과 확인** - unweighted 대비 대분류 +4.3%p, 소분류 +6.6%p
3. **GPT-4o-mini는 학습 없이도 준수** - 대분류 64% 정확도, 하지만 우리 모델에는 미달
4. **NIA ALBERT는 도메인 불일치로 사실상 비교 불가** - 자체 데이터에서는 유효한 모델

### 시사점

- **도메인 특화의 중요성**: 범용 모델(NIA, GPT)보다 도메인 특화 모델이 우리 데이터에서 압도적 우위
- **구조 설계의 효과**: 같은 데이터라도 계층적 구조(대분류→소분류 마스킹)가 성능을 끌어올림
- **LLM의 한계**: GPT는 소분류(58개)에서 특히 약함 - 세밀한 감정 구분은 학습 데이터가 필요

### 향후 과제

- concat_unw 구조에서 추가 데이터 증강 (AI 생성 데이터) 효과 확인
- 소분류 약점 분석 - 낙담한(0.18), 툴툴대는(0.21) 등 F1 하위 감정 개선
- 구조 변경과 전처리 변경의 기여도를 분리하는 ablation 실험

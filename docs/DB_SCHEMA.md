# Banuzil 데이터베이스 스키마

> Supabase(PostgreSQL) 기준  
> 최초 작성: 2026-05-22  
> 조회 방법: `GET {SUPABASE_URL}/rest/v1/` OpenAPI spec 직접 파싱

---

## 테이블 목록

| 테이블 | 설명 |
|--------|------|
| [`users`](#users) | 회원 정보 |
| [`user_attachments`](#user_attachments) | 애착 유형 검사 결과 |
| [`friendships`](#friendships) | 친구 관계 (커플 매칭) |
| [`mediation_sessions`](#mediation_sessions) | EFT 상담 세션 |
| [`mediation_records`](#mediation_records) | 라운드별 발화 및 AI 응답 기록 |
| [`mediation_reports`](#mediation_reports) | 상담 종료 후 최종 보고서 |
| [`session_embeddings`](#session_embeddings) | RAG용 세션 사이클 임베딩 (AI 서버 전용) |

---

## ERD (관계 요약)

```
users ──┬──< user_attachments
        ├──< friendships (from_user_id)
        ├──< friendships (to_user_id)
        ├──< mediation_sessions (initiator_id)
        ├──< mediation_sessions (participant_id)
        ├──< mediation_records
        └──< mediation_reports

mediation_sessions ──< mediation_records
mediation_sessions ──< mediation_reports
mediation_sessions ──< session_embeddings (ON DELETE CASCADE)
```

---

## users

회원 기본 정보. `gender`와 `mbti`는 EFT 상담 프롬프트에 활용된다.

| 컬럼 | 타입 | NOT NULL | 설명 |
|------|------|----------|------|
| `user_id` | bigint | ✅ PK | 자동 증가 |
| `email` | varchar | ✅ | 로그인 이메일 |
| `nickname` | varchar | ✅ | 표시 이름 |
| `password` | varchar | ✅ | 해시 저장 |
| `gender` | varchar | | `"female"` / `"male"` |
| `mbti` | varchar | | 4글자 (예: `"INFP"`) |
| `friend_code` | varchar | | 친구 초대 코드 |
| `created_at` | timestamp | | 가입 시각 |
| `updated_at` | timestamp | | 수정 시각 |
| `deleted_at` | timestamp | | 탈퇴 시각 (soft delete) |

---

## user_attachments

ECR-R 설문 기반 애착 유형 검사 결과. 상담 시작 전 필수 입력.

| 컬럼 | 타입 | NOT NULL | 설명 |
|------|------|----------|------|
| `attachment_id` | bigint | ✅ PK | |
| `user_id` | bigint | ✅ FK → users | |
| `anxiety_score` | double precision | ✅ | 불안 점수 (ECR-R 기준, 컷오프 2.61) |
| `avoidance_score` | double precision | ✅ | 회피 점수 (ECR-R 기준, 컷오프 2.33) |
| `type` | varchar | ✅ | 분류 결과: `"안정형"` / `"불안형"` / `"거부회피형"` / `"공포회피형"` |
| `created_at` | timestamp | | |
| `updated_at` | timestamp | | |

**비고**: `anxiety_score`, `avoidance_score` 및 `type`이 AI 서버의 `CoupleProfile` 생성에 직접 사용된다.

---

## friendships

커플 또는 친구 연결 관계. 상담 세션은 이 관계를 기반으로 생성된다.

| 컬럼 | 타입 | NOT NULL | 설명 |
|------|------|----------|------|
| `friendship_id` | bigint | ✅ PK | |
| `from_user_id` | bigint | ✅ FK → users | 요청 보낸 사람 |
| `to_user_id` | bigint | ✅ FK → users | 요청 받은 사람 |
| `created_at` | timestamp | | |
| `updated_at` | timestamp | | |

---

## mediation_sessions

EFT 상담 세션. 1세션 = 1커플의 1회 상담 전체.  
`eft_stage` ~ `cycle_skip_until` 컬럼은 AI 서버가 직접 관리한다.

| 컬럼 | 타입 | NOT NULL | 기본값 | 설명 |
|------|------|----------|--------|------|
| `session_id` | bigint | ✅ PK | auto | |
| `initiator_id` | bigint | ✅ FK → users | | 상담 시작한 사람 |
| `participant_id` | bigint | FK → users | | 상대방 (나중에 합류) |
| `current_round` | integer | ✅ | | 현재 진행 중인 라운드 번호 |
| `status` | varchar | ✅ | | `"active"` / `"completed"` 등 |
| `eft_stage` | integer | | `1` | 현재 EFT 단계 (1/2/3). 단방향 증가 |
| `stage_rounds` | jsonb | | `{"1":0,"2":0,"3":0}` | 단계별 누적 라운드 수 |
| `stage_progress` | integer | | `0` | 현재 단계 진행도 0~100 |
| `detected_signals` | jsonb | | `{"f":{},"m":{}}` | 양측 EFT 신호 누적 상태 |
| `cycle_definition` | text | | `''` | 부정적 상호작용 사이클 정의 텍스트 |
| `cycle_skip_until` | integer | | `0` | 사이클 재시도 기준 라운드 (0=즉시 가능) |
| `rag_context` | text | | `''` | RAG 검색 결과(과거 유사 세션 보고서 전문). 사이클 정의 시점에 1회 저장 |
| `created_at` | timestamp | | | |
| `updated_at` | timestamp | | | |

**detected_signals 구조 예시**:
```json
{
  "f": {
    "emotion": true,
    "patternAware": false,
    "otherSide": false,
    "relationConcern": true,
    "vulnerability": false,
    "empathy": false,
    "withdrawer_reengagement": false,
    "blamer_softening": false
  },
  "m": { ... }
}
```

**AI 서버 관리 컬럼**: `eft_stage`, `stage_rounds`, `stage_progress`, `detected_signals`, `cycle_definition`, `cycle_skip_until`, `rag_context`  
**Spring이 읽는 컬럼**: `eft_stage` (UI 단계 표시), `cycle_definition` (사이클 동의 UI)  
**Spring 미참조 컬럼**: `rag_context`는 AI 서버 전용. Spring은 읽지도 쓰지도 않음

---

## mediation_records

라운드별 사용자 발화(`content`)와 AI 상담사 응답(`ai_response`).  
세션당 최대 `current_round × 2`개 row (여성 + 남성 각 1개).

| 컬럼 | 타입 | NOT NULL | 설명 |
|------|------|----------|------|
| `record_id` | bigint | ✅ PK | |
| `session_id` | bigint | ✅ FK → mediation_sessions | |
| `user_id` | bigint | ✅ FK → users | 발화한 사람 |
| `content` | text | ✅ | 사용자 발화 내용 |
| `ai_response` | text | | AI 상담사 응답. AI 처리 전 NULL |
| `round_number` | integer | ✅ | 라운드 번호 |
| `created_at` | timestamp | | |
| `updated_at` | timestamp | | |

**저장 흐름**:
```
① Spring: INSERT (content=발화, ai_response=NULL)
② Spring: AI 서버 호출 (session_id, f_reply, m_reply)
③ AI 서버: 처리 완료 후 해당 row UPDATE (ai_response=응답)
```

---

## mediation_reports

상담 종료 시 AI가 생성하는 최종 보고서. 세션당 2개 row (여성/남성 각각).

| 컬럼 | 타입 | NOT NULL | 설명 |
|------|------|----------|------|
| `report_id` | bigint | ✅ PK | |
| `session_id` | bigint | ✅ FK → mediation_sessions | |
| `user_id` | bigint | ✅ FK → users | 보고서 대상자 |
| `emotion_summary` | text | ✅ | 나의 생각과 감정 정리 |
| `partner_understanding` | text | ✅ | 파트너의 입장과 감정 이해 |
| `mediation_plans` | text | ✅ | 중재안 3개 |
| `recommended_dialogues` | text | ✅ | 추천 대화법 (중재안별 예시) |
| `created_at` | timestamptz | | |
| `updated_at` | timestamptz | | |

**생성 주체**: AI 서버 (`services/report_service.py`)  
**조회 주체**: Spring → 프론트엔드 카드/탭 UI

---

## session_embeddings

RAG(동일 커플 과거 세션 검색)용 임베딩 저장소. **AI 서버 전용 테이블 — Spring 미참조.**  
세션 종료 시 사이클 정의를 벡터화하여 1개 row 저장. 사이클 정의가 없는 세션은 저장하지 않음.

> pgvector 확장 필요 (`CREATE EXTENSION vector`).  
> 생성 SQL: `scripts/rag_migration.sql` / 롤백: `scripts/rag_rollback.sql`

| 컬럼 | 타입 | NOT NULL | 설명 |
|------|------|----------|------|
| `id` | serial | ✅ PK | |
| `session_id` | int | ✅ FK → mediation_sessions (UNIQUE, ON DELETE CASCADE) | 세션당 1개 |
| `couple_key` | varchar | ✅ | `min(id)_max(id)` 형식. 동일 커플 검색 필터 |
| `eft_final_stage` | smallint | | 세션 종료 시점 EFT 단계 (기본 3) |
| `cycle_text` | text | ✅ | 사이클 정의 원문 (디버깅/확인용) |
| `summary_text` | text | ✅ | 보고서 전문 (검색 히트 시 프롬프트 주입용) |
| `embedding` | vector(1536) | ✅ | 사이클 정의 임베딩 (text-embedding-3-small) |
| `created_at` | timestamptz | | |

**인덱스**:
- `idx_session_embeddings_couple` — `couple_key` (커플 필터)
- `idx_session_embeddings_vector` — `embedding` ivfflat (벡터 유사도 검색)

**검색 함수**: `match_best_couple_session(query_embedding, target_couple_key, exclude_session_id, similarity_threshold)`  
→ 같은 커플 + 현재 세션 제외 + 유사도 ≥ threshold(기본 0.75) 중 최고 1건 반환

**생성 주체**: AI 서버 (`services/rag/embedding_service.py`)  
**조회 주체**: AI 서버 (`services/rag/retrieval_service.py`, RPC 호출)  
**embedding 저장 형식**: pgvector는 `"[0.1,0.2,...]"` 문자열로 입력

---

## 주요 흐름 요약

### 1. 회원가입 & 설문
```
users INSERT → user_attachments INSERT
```

### 2. 친구 매칭
```
friendships INSERT (from_user_id, to_user_id)
```

### 3. 상담 시작
```
mediation_sessions INSERT (initiator_id, participant_id, current_round=1, status='active')
```

### 4. 라운드 진행 (반복)
```
mediation_records INSERT × 2 (여성, 남성 발화)
→ AI 서버 호출
→ mediation_records UPDATE (ai_response 채움)
→ mediation_sessions UPDATE (eft_stage, detected_signals, stage_progress 등)
```

### 4-1. 사이클 정의 생성 (1→2단계 전환 시) + RAG 검색
```
POST /ai/cycle (define 모드)
→ mediation_sessions UPDATE (cycle_definition)
→ [RAG] 사이클 정의 벡터화 → match_best_couple_session RPC
   → 유사도 ≥ 0.75 과거 세션 발견 시
     mediation_sessions UPDATE (rag_context = 과거 보고서 전문)
→ 이후 2단계 라운드에서 rag_context를 시스템 프롬프트에 주입
```

### 5. 상담 종료
```
mediation_reports INSERT × 2 (여성 보고서, 남성 보고서)
mediation_sessions UPDATE (status='completed')   ← Spring이 수행
→ [RAG] 사이클 정의 벡터화 → session_embeddings INSERT (미래 세션 검색용)
```

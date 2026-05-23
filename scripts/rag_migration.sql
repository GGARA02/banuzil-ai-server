-- ============================================================
-- RAG 기능 DB 마이그레이션 (Supabase SQL Editor에서 실행)
-- 동일 커플 과거 사이클 기반 검색
-- ============================================================

-- 1. pgvector 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. 세션 임베딩 테이블
CREATE TABLE IF NOT EXISTS session_embeddings (
  id              serial        PRIMARY KEY,
  session_id      int           NOT NULL UNIQUE
                                REFERENCES mediation_sessions(session_id)
                                ON DELETE CASCADE,
  couple_key      varchar       NOT NULL,        -- min(id)_max(id)
  eft_final_stage smallint      DEFAULT 3,
  cycle_text      text          NOT NULL,        -- 사이클 정의 원문 (디버깅용)
  summary_text    text          NOT NULL,        -- 보고서 전문 (프롬프트 주입용)
  embedding       vector(1536)  NOT NULL,        -- 사이클 정의 벡터 (검색용)
  created_at      timestamptz   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_embeddings_couple
  ON session_embeddings (couple_key);

CREATE INDEX IF NOT EXISTS idx_session_embeddings_vector
  ON session_embeddings
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 10);

-- 3. mediation_sessions에 rag_context 컬럼 추가
ALTER TABLE mediation_sessions
  ADD COLUMN IF NOT EXISTS rag_context text DEFAULT '';

-- 4. 벡터 검색 RPC 함수: 유사도 기준 이상 중 최고 1건
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

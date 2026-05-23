-- ============================================================
-- RAG 기능 롤백 (rag_migration.sql 되돌리기)
-- Supabase SQL Editor에서 실행
--
-- ⚠️ vector 확장은 일부러 삭제하지 않는다 (다른 기능이 쓸 수 있음).
--    확장은 켜져 있어도 부작용 없음.
-- ============================================================

-- 1. 검색 함수 삭제 (인자 타입까지 명시해야 정확히 매칭됨)
DROP FUNCTION IF EXISTS match_best_couple_session(
  vector(1536), varchar, int, float
);

-- 2. 임베딩 테이블 삭제 (인덱스도 함께 자동 삭제됨)
DROP TABLE IF EXISTS session_embeddings;

-- 3. mediation_sessions의 rag_context 컬럼 삭제
--    (RAG 전용 컬럼이므로 다른 데이터 영향 없음)
ALTER TABLE mediation_sessions
  DROP COLUMN IF EXISTS rag_context;

-- vector 확장은 남겨둔다. (삭제하려면 아래 주석 해제 — 권장하지 않음)
-- DROP EXTENSION IF EXISTS vector;

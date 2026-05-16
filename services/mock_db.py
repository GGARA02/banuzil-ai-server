# ============================================================
# services/mock_db.py — 인메모리 Mock Supabase 클라이언트
#
# SupabaseClient와 동일한 get/insert/update/delete 인터페이스.
# 테스트 클라이언트 전용 — 실제 DB 없이 AI 응답을 테스트할 수 있다.
# ============================================================

import re
import logging
from copy import deepcopy

logger = logging.getLogger(__name__)


class MockSupabaseClient:
    """인메모리 딕셔너리 기반 Mock Supabase."""

    def __init__(self):
        self._tables: dict[str, list[dict]] = {}
        self._auto_ids: dict[str, int] = {}

    def reset(self):
        self._tables.clear()
        self._auto_ids.clear()

    def _get_table(self, table: str) -> list[dict]:
        if table not in self._tables:
            self._tables[table] = []
        return self._tables[table]

    def _next_id(self, table: str) -> int:
        self._auto_ids.setdefault(table, 0)
        self._auto_ids[table] += 1
        return self._auto_ids[table]

    def _match(self, row: dict, filters: dict) -> bool:
        for key, val in filters.items():
            if key in ("order", "select"):
                continue

            if key == "user_id" and val.startswith("in."):
                ids_str = val[3:].strip("()")
                ids = [int(x.strip()) for x in ids_str.split(",")]
                if row.get("user_id") not in ids:
                    return False
                continue

            parts = val.split(".", 1)
            if len(parts) != 2:
                continue
            op, operand = parts

            row_val = row.get(key)
            if op == "eq":
                try:
                    operand = type(row_val)(operand) if row_val is not None else operand
                except (ValueError, TypeError):
                    pass
                if row_val != operand:
                    return False
            elif op == "neq":
                try:
                    operand = type(row_val)(operand) if row_val is not None else operand
                except (ValueError, TypeError):
                    pass
                if row_val == operand:
                    return False
            elif op == "in":
                ids_str = operand.strip("()")
                ids = [x.strip() for x in ids_str.split(",")]
                if str(row_val) not in ids:
                    return False

        return True

    async def get(self, table: str, filters: dict | None = None) -> list[dict]:
        rows = self._get_table(table)
        if not filters:
            return deepcopy(rows)

        result = [deepcopy(r) for r in rows if self._match(r, filters)]

        order = filters.get("order")
        if order:
            for part in reversed(order.split(",")):
                part = part.strip()
                desc = part.endswith(".desc")
                col = part.replace(".asc", "").replace(".desc", "")
                result.sort(key=lambda r: r.get(col, ""), reverse=desc)

        return result

    async def insert(self, table: str, data: dict | list[dict]) -> list[dict]:
        rows = self._get_table(table)
        items = data if isinstance(data, list) else [data]
        result = []
        for item in items:
            row = deepcopy(item)
            rows.append(row)
            result.append(deepcopy(row))
        logger.debug(f"[MockDB] INSERT {table}: {len(items)}건")
        return result

    async def update(self, table: str, filters: dict, data: dict) -> list[dict]:
        rows = self._get_table(table)
        result = []
        for row in rows:
            if self._match(row, filters):
                row.update(deepcopy(data))
                result.append(deepcopy(row))
        logger.debug(f"[MockDB] UPDATE {table}: {len(result)}건")
        return result

    async def delete(self, table: str, filters: dict) -> list[dict]:
        rows = self._get_table(table)
        to_del = [r for r in rows if self._match(r, filters)]
        for r in to_del:
            rows.remove(r)
        logger.debug(f"[MockDB] DELETE {table}: {len(to_del)}건")
        return deepcopy(to_del)

    async def ping(self) -> bool:
        return True


mock_supa = MockSupabaseClient()


def seed_mock_session(
    session_id: int,
    f_anxiety: float, f_avoidance: float, f_mbti: str,
    m_anxiety: float, m_avoidance: float, m_mbti: str,
):
    """테스트용 세션 데이터를 mock DB에 시드."""
    mock_supa.reset()

    f_user_id = 100
    m_user_id = 200

    # users
    mock_supa._get_table("users").extend([
        {
            "user_id": f_user_id,
            "gender": "female",
            "mbti": f_mbti.upper() if f_mbti else "",
            "nickname": "테스트여성",
        },
        {
            "user_id": m_user_id,
            "gender": "male",
            "mbti": m_mbti.upper() if m_mbti else "",
            "nickname": "테스트남성",
        },
    ])

    # user_attachments
    mock_supa._get_table("user_attachments").extend([
        {
            "user_id": f_user_id,
            "anxiety_score": f_anxiety,
            "avoidance_score": f_avoidance,
        },
        {
            "user_id": m_user_id,
            "anxiety_score": m_anxiety,
            "avoidance_score": m_avoidance,
        },
    ])

    # mediation_sessions
    mock_supa._get_table("mediation_sessions").append({
        "session_id": session_id,
        "initiator_id": f_user_id,
        "participant_id": m_user_id,
        "status": "active",
        "eft_stage": 1,
        "stage_rounds": {"1": 0, "2": 0, "3": 0},
        "stage_progress": 0,
        "detected_signals": None,
        "cycle_definition": "",
        "cycle_skip_until": 0,
        "current_round": 1,
    })

    # mediation_records (빈 상태)
    # mediation_reports (빈 상태)

    logger.info(f"[MockDB] 세션 #{session_id} 시드 완료 (f={f_user_id}, m={m_user_id})")

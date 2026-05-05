# ============================================================
# services/session_store.py — 메모리 기반 세션 저장소
#
# 현재: Python 프로세스 메모리 딕셔너리
# 차후: Redis 교체 시 이 파일만 수정 (인터페이스 동일 유지)
#
# 서버 재시작 시 세션 초기화됨 (개발 단계에서는 무방)
# ============================================================

from typing import Optional
from datetime import datetime, timedelta

_store: dict[str, dict] = {}
SESSION_TTL_HOURS = 24


def session_get(session_id: str) -> Optional[dict]:
    """세션 조회. 없거나 만료되면 None 반환."""
    data = _store.get(session_id)
    if data is None:
        return None
    if datetime.now() > data["_expires_at"]:
        del _store[session_id]
        return None
    return data


def session_set(session_id: str, data: dict) -> None:
    """세션 저장/갱신. 호출마다 만료 시간 리셋."""
    data["_expires_at"] = datetime.now() + timedelta(hours=SESSION_TTL_HOURS)
    _store[session_id] = data


def session_delete(session_id: str) -> None:
    _store.pop(session_id, None)


def session_exists(session_id: str) -> bool:
    return session_get(session_id) is not None

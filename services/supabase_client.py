# ============================================================
# services/supabase_client.py — Supabase REST API 클라이언트
#
# httpx 기반으로 Supabase PostgREST API와 통신.
# 추가 패키지 불필요 (httpx는 이미 설치됨).
#
# 사용 예:
#   from services.supabase_client import supa
#   user = await supa.get("users", {"session_id": "eq.abc123"})
# ============================================================

import httpx
from typing import Any
from config.settings import SUPABASE_URL, SUPABASE_KEY


class SupabaseClient:
    """Supabase PostgREST REST API 클라이언트 (싱글턴)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._base_url = f"{SUPABASE_URL}/rest/v1"
        self._headers = {
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",  # INSERT/UPDATE 시 결과 반환
        }

    # ── 기본 CRUD ────────────────────────────────────────────

    async def get(self, table: str, filters: dict[str, str] | None = None) -> list[dict]:
        """
        SELECT. filters 예: {"session_id": "eq.abc123", "eft_stage": "gte.2"}
        PostgREST 연산자: eq / neq / gt / gte / lt / lte / like / ilike / is / in
        """
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{self._base_url}/{table}",
                headers=self._headers,
                params=filters or {},
            )
            res.raise_for_status()
            return res.json()

    async def insert(self, table: str, data: dict | list[dict]) -> list[dict]:
        """INSERT. 단건 dict 또는 복수 list[dict]"""
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{self._base_url}/{table}",
                headers=self._headers,
                json=data,
            )
            res.raise_for_status()
            return res.json()

    async def update(self, table: str, filters: dict[str, str], data: dict) -> list[dict]:
        """UPDATE. filters로 대상 행 특정 후 data로 업데이트"""
        async with httpx.AsyncClient() as client:
            res = await client.patch(
                f"{self._base_url}/{table}",
                headers=self._headers,
                params=filters,
                json=data,
            )
            res.raise_for_status()
            return res.json()

    async def delete(self, table: str, filters: dict[str, str]) -> list[dict]:
        """DELETE. filters로 대상 행 특정"""
        async with httpx.AsyncClient() as client:
            res = await client.delete(
                f"{self._base_url}/{table}",
                headers=self._headers,
                params=filters,
            )
            res.raise_for_status()
            return res.json()

    async def rpc(self, function_name: str, params: dict | None = None) -> list[dict]:
        """
        Postgres 함수(RPC) 호출. POST /rest/v1/rpc/{function_name}
        벡터 검색 등 SQL 함수 실행용.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                f"{self._base_url}/rpc/{function_name}",
                headers=self._headers,
                json=params or {},
            )
            res.raise_for_status()
            return res.json()

    async def ping(self) -> bool:
        """연결 상태 확인"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{self._base_url}/",
                    headers=self._headers,
                )
                return res.status_code < 500
        except Exception:
            return False


# 싱글턴 인스턴스
supa = SupabaseClient()

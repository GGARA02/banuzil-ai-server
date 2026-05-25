# ============================================================
# main.py — FastAPI 서버 진입점
# 실행: python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# ============================================================

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from routers import emotion, counseling, mock_router
from services.supabase_client import supa

app = FastAPI(
    title       = "바느질 AI 서버",
    description = "EFT 상담 AI + KoELECTRA 감성 분석 서버 (DB-centric)",
    version     = "4.0.0",
)

# CORS — 개발 중 전체 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(emotion.router)
app.include_router(counseling.router)

# Mock 라우터는 개발 환경에서만 등록한다.
# 운영에 노출되면 /ai/mock/init-session 한 번으로 supa가 전역 mock으로 전환되어
# Spring 등 실제 트래픽이 모두 mock DB를 바라보게 되는 사고가 난다.
if os.getenv("ENABLE_MOCK", "false").lower() == "true":
    app.include_router(mock_router.router)

# 테스트 클라이언트 HTML 서빙
@app.get("/test")
async def test_client():
    path = os.path.join(os.path.dirname(__file__), "test_client.html")
    return FileResponse(path)

# 감성 분석 테스트 페이지
@app.get("/emotion-test")
async def emotion_test():
    path = os.path.join(os.path.dirname(__file__), "emotion_test.html")
    return FileResponse(path)

@app.get("/health/supabase")
async def supabase_health():
    """Supabase 연결 상태 확인"""
    ok = await supa.ping()
    return {"supabase": "connected" if ok else "error"}


@app.get("/")
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "service": "바느질 AI 서버",
        "version": "3.0.0",
        "docs":    f"{base}/docs",
        "test":    f"{base}/test",
        "endpoints": {
            "라운드 분석":    "POST /ai/round-analyze",
            "사이클 절차":    "POST /ai/cycle",
            "최종 보고서":    "POST /ai/report",
            "감성 분석 단일": "POST /emotion/analyze",
            "감성 분석 배치": "POST /emotion/analyze/batch",
            "감성 분석 헬스": "GET  /emotion/health",
        }
    }

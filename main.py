# ============================================================
# main.py — FastAPI 서버 진입점
# 실행: python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from routers import emotion, counseling

app = FastAPI(
    title       = "바느질 AI 서버",
    description = "EFT 상담 AI + KoELECTRA 감성 분석 서버",
    version     = "2.0.0",
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

# 테스트 클라이언트 HTML 서빙
@app.get("/test")
async def test_client():
    path = os.path.join(os.path.dirname(__file__), "test_client.html")
    return FileResponse(path)

@app.get("/")
async def root():
    return {
        "service": "바느질 AI 서버",
        "version": "2.0.0",
        "docs":    "http://localhost:8000/docs",
        "test":    "http://localhost:8000/test",
        "endpoints": {
            "상담 세션 생성": "POST /counseling/session",
            "상담 라운드":    "POST /counseling/round",
            "사이클 절차":    "POST /counseling/cycle",
            "종료 동의":      "POST /counseling/end",
            "최종 보고서":    "POST /counseling/report",
            "감성 분석 단일": "POST /emotion/analyze",
        }
    }

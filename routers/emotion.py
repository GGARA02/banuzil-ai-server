# ============================================================
# routers/emotion.py — 감정 분석 API 라우터
# ============================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services.emotion_service import EmotionService

router  = APIRouter(prefix="/emotion", tags=["emotion"])
service = EmotionService()


class EmotionRequest(BaseModel):
    text:      str
    gender:    Optional[str] = "미상"
    situation: Optional[str] = "연애"

    class Config:
        json_schema_extra = {
            "example": {
                "text":      "왜 연락을 안 하는 걸까? 화가 난다.",
                "gender":    "여성",
                "situation": "연애"
            }
        }


class EmotionBatchRequest(BaseModel):
    items: list[EmotionRequest]


@router.post("/analyze")
async def analyze_emotion(request: EmotionRequest):
    """
    단일 발화 감정 분석.

    - **text**: 분석할 발화 문장
    - **gender**: 성별 (여성/남성/미상, 기본값: 미상)
    - **situation**: 상황키워드 (연애/결혼/출산 등, 기본값: 연애)
    """
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text가 비어있습니다.")

    return service.analyze(
        text      = request.text,
        gender    = request.gender,
        situation = request.situation,
    )


@router.post("/analyze/batch")
async def analyze_emotion_batch(request: EmotionBatchRequest):
    """여러 발화 일괄 감정 분석."""
    if not request.items:
        raise HTTPException(status_code=400, detail="items가 비어있습니다.")

    results = service.analyze_batch([
        {
            "text":      item.text,
            "gender":    item.gender,
            "situation": item.situation,
        }
        for item in request.items
    ])
    return {"results": results}


@router.get("/health")
async def health_check():
    """서버 상태 확인"""
    return {
        "status": "ok",
        "model":  "concat_unweight (hierarchical)",
    }

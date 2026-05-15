# ============================================================
# Dockerfile — banuzil-ai-server (FastAPI + KoELECTRA + LangGraph)
# Python 3.11 slim | PyTorch CPU | 포트 8000
# ============================================================

FROM python:3.11-slim

# 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 의존성 설치 ──────────────────────────────────────────────
# torch를 CPU 전용으로 먼저 설치 (CUDA 제외 → 이미지 약 2GB 절감)
RUN pip install --no-cache-dir \
    torch==2.3.1 \
    --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── 소스 복사 ────────────────────────────────────────────────
COPY . .

# ── 런타임 설정 ──────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# 모델 파일이 마운트됐는지 확인 후 서버 시작
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

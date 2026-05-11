# ============================================================
# emotion_cli.py — 감성 분석 커맨드라인 테스터
#
# 실행: python emotion_cli.py
# 서버를 자동으로 켜고, 발화를 입력받아 감성 분석 결과를 출력
# ============================================================

import subprocess
import sys
import time
import requests
import json

SERVER_URL = "http://localhost:8000"
ANALYZE_URL = f"{SERVER_URL}/emotion/analyze"
HEALTH_URL  = f"{SERVER_URL}/emotion/health"


# ── 서버 실행 ──────────────────────────────────────────────
def start_server():
    print("서버 시작 중...")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process


# ── 서버 준비 대기 ─────────────────────────────────────────
def wait_for_server(timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            res = requests.get(HEALTH_URL, timeout=2)
            if res.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
        print(".", end="", flush=True)
    return False


# ── 결과 출력 ──────────────────────────────────────────────
def print_result(result: dict):
    print("\n" + "─" * 45)

    # 대분류
    cats = result.get("category", [])
    print("📊 대분류 감정:")
    for c in cats:
        bar = "█" * int(c["score"] * 20)
        print(f"   {c['label']:<10} {bar} {c['score']:.2f}")

    # 소분류
    dets = result.get("detail", [])
    print("\n🔍 소분류 감정:")
    for d in dets:
        bar = "█" * int(d["score"] * 20)
        print(f"   {d['label']:<16} {bar} {d['score']:.2f}")

    print("─" * 45)


# ── 메인 루프 ──────────────────────────────────────────────
def main():
    # 서버가 이미 켜져있는지 확인
    already_running = False
    try:
        res = requests.get(HEALTH_URL, timeout=2)
        if res.status_code == 200:
            already_running = True
    except requests.exceptions.ConnectionError:
        pass

    server_process = None
    if already_running:
        print("✅ 서버가 이미 실행 중입니다.")
    else:
        server_process = start_server()
        print("\n", end="")
        ok = wait_for_server()
        if not ok:
            print("\n❌ 서버 시작 실패. 직접 서버를 실행해 주세요.")
            server_process.terminate()
            return
        print("\n✅ 서버 준비 완료!")

    # 성별 / 상황 설정
    print("\n" + "=" * 45)
    print("  바느질 AI 감성 분석 CLI")
    print("=" * 45)
    print("성별 입력 (여성 / 남성 / 미상, 기본값=미상): ", end="")
    gender = input().strip() or "미상"
    print("상황 입력 (연애 / 결혼 / 기타, 기본값=연애): ", end="")
    situation = input().strip() or "연애"
    print(f"\n✔ 설정: 성별={gender}, 상황={situation}")
    print("종료하려면 'q' 또는 빈 줄 입력\n")

    # 발화 입력 루프
    while True:
        print("💬 발화 입력: ", end="")
        text = input().strip()

        if text.lower() in ("q", "quit", "exit", ""):
            print("종료합니다.")
            break

        try:
            res = requests.post(
                ANALYZE_URL,
                json={
                    "text":      text,
                    "gender":    gender,
                    "situation": situation,
                },
                timeout=10,
            )

            if res.status_code == 200:
                print_result(res.json())
            else:
                print(f"❌ 오류: {res.status_code} — {res.text}")

        except requests.exceptions.ConnectionError:
            print("❌ 서버 연결 끊김.")
            break
        except Exception as e:
            print(f"❌ 예외: {e}")

    # 서버 종료
    if server_process:
        print("서버 종료 중...")
        server_process.terminate()


if __name__ == "__main__":
    main()

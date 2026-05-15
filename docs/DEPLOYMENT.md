# 배포 가이드 — AWS EC2

## 1. 서버 인프라 정보

| 항목 | 값 |
|------|-----|
| 클라우드 | AWS EC2 |
| 인스턴스 유형 | t3.small (2 vCPU, 2GB RAM) |
| OS | Ubuntu 26.04 LTS |
| 스토리지 | 20 GiB (gp3) |
| 리전 | ap-southeast-2 (시드니) |
| 탄력적 IP | `15.135.116.29` |
| API 주소 | `http://15.135.116.29:8000` |
| Swagger UI | `http://15.135.116.29:8000/docs` |

### 보안 그룹 (launch-wizard-1)

| 포트 | 프로토콜 | 소스 | 용도 |
|------|----------|------|------|
| 22 | TCP | 0.0.0.0/0 | SSH 접속 |
| 80 | TCP | 0.0.0.0/0 | HTTP |
| 8000 | TCP | 0.0.0.0/0 | FastAPI 서버 |

---

## 2. SSH 접속

```bash
ssh -i "<pem 파일 경로>/banuzil-key.pem" ubuntu@15.135.116.29
```

### .pem 키 파일 주의사항
- `banuzil-key.pem`은 EC2 접속용 비밀 키 파일
- **절대 GitHub에 커밋하지 말 것** (`.gitignore`에 `*.pem` 등록됨)
- 메일/메신저로 전송 금지 — USB, 물리적 복사 권장
- 분실 시 EC2 접속 불가 (새 키 페어 생성 필요)

---

## 3. 배포 구조

```
EC2 서버 (/home/ubuntu/banuzil-ai-server/)
├── 코드        ← git clone으로 다운로드 (GitHub에서)
├── .env        ← 서버에 직접 관리 (scp로 전송)
├── models/     ← 볼륨 마운트 (scp로 전송)
│   └── concat_unweight/
│       └── best_model.pt   (488MB, KoELECTRA 파인튜닝 가중치)
└── Docker      ← Dockerfile + docker-compose.yml로 실행
```

### GitHub에 없는 파일 (서버에 직접 관리)
- `.env` — API 키, Supabase 인증 등 환경 변수
- `models/concat_unweight/best_model.pt` — 감정 분류 모델 가중치 (488MB)

이 두 파일은 `git clone`만으로는 서버에 없으므로 **수동 전송 필수**.

---

## 4. 최초 배포 절차

### 4-1. EC2 접속 후 Docker 설치
```bash
sudo apt update && sudo apt install -y docker.io docker-compose
sudo systemctl start docker
sudo usermod -aG docker ubuntu
```

### 4-2. 코드 다운로드
```bash
git clone https://github.com/GGARA02/banuzil-ai-server.git
cd banuzil-ai-server
```

### 4-3. 모델 파일 전송 (로컬 CMD에서)
```bash
scp -i "<pem 경로>/banuzil-key.pem" \
    "<로컬 경로>/models/concat_unweight/best_model.pt" \
    ubuntu@15.135.116.29:~/banuzil-ai-server/models/concat_unweight/
```

### 4-4. .env 파일 전송 (로컬 CMD에서)
```bash
scp -i "<pem 경로>/banuzil-key.pem" \
    "<로컬 경로>/.env" \
    ubuntu@15.135.116.29:~/banuzil-ai-server/.env
```

### 4-5. Docker 빌드 및 실행
```bash
cd ~/banuzil-ai-server
sudo docker compose up --build -d
```

---

## 5. 코드 업데이트 (재배포)

코드를 수정하고 GitHub에 push한 후:

```bash
# EC2에서 실행
cd ~/banuzil-ai-server
git pull
sudo docker compose up --build -d
```

> `.env`나 모델 파일이 변경된 경우에만 별도 scp 전송 필요.
> 코드만 변경된 경우 `git pull + docker compose up --build -d`로 충분.

---

## 6. 운영 명령어

```bash
# 컨테이너 상태 확인
sudo docker ps

# 로그 확인
sudo docker logs banuzil-ai-server

# 최근 로그만 확인
sudo docker logs banuzil-ai-server --tail 30

# 서버 재시작
sudo docker compose restart ai-server

# 서버 중지
sudo docker compose down

# 서버 시작 (빌드 없이)
sudo docker compose up -d

# 전체 재빌드 (Dockerfile 변경 시)
sudo docker compose up --build -d

# 안 쓰는 Docker 이미지 정리 (디스크 절약)
sudo docker image prune -f
```

---

## 7. 비용

| 항목 | 월 비용 (USD) |
|------|---------------|
| EC2 t3.small (온디맨드) | ~$15 |
| EBS 20GB gp3 | ~$2 |
| 탄력적 IP (연결된 상태) | 무료 |
| 탄력적 IP (연결 안 된 상태) | ~$4 |
| **합계** | **~$17/월** |

> AWS 크레딧 $100 잔여 (2026.11.15 만료)
> 탄력적 IP는 EC2에 연결된 상태에서는 무료. EC2 중지 시에도 요금 발생하니 주의.

---

## 8. 향후 TODO

- [ ] **GitHub Actions** — push 시 EC2 자동 배포 (git pull + docker restart)
- [ ] **도메인 연결** — IP 대신 도메인 주소 사용
- [ ] **HTTPS (SSL)** — Let's Encrypt 인증서 발급 + Nginx 리버스 프록시
- [ ] **인스턴스 사양 업그레이드** — RAM 부족 시 t3.medium (4GB)으로 변경

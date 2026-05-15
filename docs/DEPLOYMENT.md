# 배포 가이드 — AWS EC2

## 1. 서버 인프라 정보

| 항목 | 값 |
|------|-----|
| 클라우드 | AWS EC2 |
| 인스턴스 유형 | t3.small (2 vCPU, 2GB RAM + Swap 2GB) |
| OS | Ubuntu 26.04 LTS |
| 스토리지 | 20 GiB (gp3) |
| 리전 | ap-southeast-2 (시드니) |
| 탄력적 IP | `15.135.116.29` |
| 도메인 | `banuzil-ai.duckdns.org` |
| API 주소 | `http://banuzil-ai.duckdns.org` |
| Swagger UI | `http://banuzil-ai.duckdns.org/docs` |
| Nginx | 80 → 8000 리버스 프록시 |

### 보안 그룹 (launch-wizard-1)

| 포트 | 프로토콜 | 소스 | 용도 |
|------|----------|------|------|
| 22 | TCP | 0.0.0.0/0 | SSH 접속 |
| 80 | TCP | 0.0.0.0/0 | HTTP |
| 8000 | TCP | 0.0.0.0/0 | FastAPI 서버 |

---

## 2. SSH 접속

### Windows (CMD/PowerShell)
```
ssh -i "<pem 파일 경로>\banuzil-key.pem" ubuntu@15.135.116.29
```

### Mac / Linux
```bash
ssh -i "<pem 파일 경로>/banuzil-key.pem" ubuntu@15.135.116.29
```

예시 (Windows):
```
ssh -i "C:\Users\<사용자명>\Documents\banuzil-key.pem" ubuntu@15.135.116.29
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

### 4-2. Nginx 설치 및 설정 (리버스 프록시)
80번 포트 → 8000번으로 전달하여 포트 번호 없이 접속 가능하게 함.
```bash
sudo apt install -y nginx
sudo bash -c 'cat > /etc/nginx/sites-available/default << EOF
server {
    listen 80;
    server_name banuzil-ai.duckdns.org;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF'
sudo systemctl restart nginx
```

> Nginx는 EC2 재부팅 시 자동 시작됨. GitHub Actions와 무관.

### 4-4. Swap 메모리 설정 (필수)
t3.small은 RAM 2GB로 모델 로딩 시 OOM 발생. Swap 2GB 추가 필수.
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 재부팅 시에도 유지되도록 영구 설정
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

확인: `free -h` 실행 시 Swap 2.0Gi 표시되면 정상.

### 4-5. 코드 다운로드
```bash
git clone https://github.com/GGARA02/banuzil-ai-server.git
cd banuzil-ai-server
```

### 4-6. 모델 파일 전송 (로컬 CMD에서)
```bash
scp -i "<pem 경로>/banuzil-key.pem" \
    "<로컬 경로>/models/concat_unweight/best_model.pt" \
    ubuntu@15.135.116.29:~/banuzil-ai-server/models/concat_unweight/
```

### 4-7. .env 파일 전송 (로컬 CMD에서)
```bash
scp -i "<pem 경로>/banuzil-key.pem" \
    "<로컬 경로>/.env" \
    ubuntu@15.135.116.29:~/banuzil-ai-server/.env
```

### 4-8. Docker 빌드 및 실행
```bash
cd ~/banuzil-ai-server
sudo docker compose up --build -d
```

---

## 5. 코드 업데이트 (재배포)

### 자동 배포 (GitHub Actions) ✅
`master` 브랜치에 push하면 자동으로 EC2에 배포돼요.

```
git push origin master
    ↓
GitHub Actions 자동 실행
    ↓
EC2에서 git pull + docker compose up --build -d
```

배포 진행 상황은 GitHub 저장소 → **Actions 탭**에서 확인 가능.
- ✅ 초록 체크 = 배포 성공
- ❌ 빨간 X = 배포 실패 (로그 클릭해서 원인 확인)

#### Actions 안정성 설정
- **재시도 3회**: SSH 연결 실패 시 30초 간격으로 최대 3회 재시도 (Docker 빌드 중 서버 부하로 인한 일시적 실패 대응)
- **중복 실행 방지**: 연속 push 시 이전 실행은 자동 취소, 최신 것만 실행

### GitHub Secrets (Actions 설정값)
| Secret | 값 | 용도 |
|--------|-----|------|
| `EC2_HOST` | `15.135.116.29` | EC2 접속 IP |
| `EC2_KEY` | `banuzil-key.pem` 내용 | EC2 SSH 키 |

### 수동 배포 (필요시)
```bash
# EC2에서 실행
cd ~/banuzil-ai-server
git pull
sudo docker compose up --build -d
```

> `.env`나 모델 파일이 변경된 경우에만 별도 scp 전송 필요.
> 코드만 변경된 경우 자동 배포로 충분.

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

# RAM / Swap 사용량 확인
free -h

# OOM(메모리 부족) 발생 이력 확인
sudo dmesg | grep -i "killed process" | tail -5
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

- [x] **GitHub Actions** — push 시 EC2 자동 배포 완료
- [x] **도메인 연결** — `banuzil-ai.duckdns.org` (DuckDNS 무료)
- [x] **Nginx 리버스 프록시** — 포트 80 → 8000 전달 (`:8000` 생략 가능)
- [x] **Swap 메모리** — 2GB Swap 설정 완료 (OOM 방지)
- [ ] **HTTPS (SSL)** — Let's Encrypt 인증서 발급
- [ ] **인스턴스 사양 업그레이드** — Swap으로도 부족 시 t3.medium (4GB)으로 변경

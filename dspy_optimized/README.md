# DSPy 최적화 파일 저장소

이 폴더에 최적화 파일(`v*.json`)이 있으면 DSPy 모듈이 자동으로 로드한다.
파일이 없으면 기존 프롬프트가 기본값으로 사용된다.

## 구조

```
dspy_optimized/
├── bullet/          # BulletDetector 최적화
│   ├── v1.json      # 첫 번째 최적화
│   └── v2.json      # 두 번째 (최신 자동 로드)
├── eval/            # EFTEvaluator 최적화
│   └── v1.json
└── neutrality/      # NeutralityJudge 최적화
    └── v1.json
```

## 최적화 실행 예시

```bash
python optimize.py --module bullet --output dspy_optimized/bullet/v1.json
```

## 버전 규칙

- 파일명: `v{숫자}.json` (예: v1.json, v2.json, v3.json)
- 자동으로 숫자가 가장 큰 파일을 최신으로 인식
- 이전 버전은 롤백용으로 보존

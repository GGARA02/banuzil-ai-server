# ============================================================
# config.py — 파인튜닝 전체 설정 중앙화
# ============================================================

# ── 경로 설정 ─────────────────────────────────────────────
DATA_TRAIN_PATH = "data/raw/감성대화말뭉치_최종데이터__Training.xlsx"
DATA_VAL_PATH   = "data/raw/감성대화말뭉치_최종데이터__Validation.xlsx"

# ── 모델 버전 관리 ─────────────────────────────────────────
# "weighted"   : 연애/결혼/출산 5배, 대인관계 2배, 나머지 0.3배 + 증강 2배
# "unweighted" : 모든 카테고리 1배, 증강 없음 (Colab baseline)
# "low_weight" : 연애/결혼/출산 3배, 대인관계 1.5배, 나머지 0.5배 + 증강 1배
MODEL_VERSION    = "unweighted"
MODEL_OUTPUT_DIR = f"models/{MODEL_VERSION}"
CHECKPOINT_DIR   = f"models/{MODEL_VERSION}_checkpoints"

# ── 베이스 모델 ────────────────────────────────────────────
BASE_MODEL_NAME = "beomi/KcELECTRA-base-v2022"

# ── 학습 하이퍼파라미터 ────────────────────────────────────
MAX_LENGTH        = 128
NUM_EPOCHS        = 5
TRAIN_BATCH_SIZE  = 32
EVAL_BATCH_SIZE   = 64
LEARNING_RATE     = 2e-5
WARMUP_RATIO      = 0.1
WEIGHT_DECAY      = 0.01
SEED              = 42

# Multi-task 손실 가중치
LOSS_WEIGHT_CATEGORY = 0.6
LOSS_WEIGHT_DETAIL   = 0.4

# ── 버전별 샘플링 / 증강 설정 자동 분기 ──────────────────
if MODEL_VERSION == "weighted":
    CATEGORY_WEIGHTS = {
        "연애/결혼/출산": 5.0,
        "대인관계":       2.0,
    }
    DEFAULT_WEIGHT     = 0.3
    AUGMENT_MULTIPLIER = 2

elif MODEL_VERSION == "low_weight":
    CATEGORY_WEIGHTS = {
        "연애/결혼/출산": 3.0,
        "대인관계":       1.5,
    }
    DEFAULT_WEIGHT     = 0.5
    AUGMENT_MULTIPLIER = 1

elif MODEL_VERSION == "unweighted":
    CATEGORY_WEIGHTS   = {}
    DEFAULT_WEIGHT     = 1.0
    AUGMENT_MULTIPLIER = 0

else:
    raise ValueError(f"알 수 없는 MODEL_VERSION: {MODEL_VERSION}")

# 카테고리 판별 키워드 매핑 (공통)
CATEGORY_KEYWORD_MAP = {
    "연애/결혼/출산": ["연애", "결혼", "출산"],
    "대인관계":       ["대인관계"],
}

# ── 텍스트 증강 설정 ───────────────────────────────────────
AUGMENT_TARGET_CATEGORY = "연애/결혼/출산"
SYNONYM_REPLACE_PROB    = 0.3
RANDOM_DELETE_PROB      = 0.1
SWAP_PROB               = 0.2

# ── 레이블 정의 ───────────────────────────────────────────
CATEGORY_LABELS = ["분노", "기쁨", "불안", "당황", "슬픔", "상처"]

DETAIL_LABELS = [
    "가난한, 불우한", "감사하는", "걱정스러운", "고립된", "괴로워하는",
    "구역질 나는", "기쁨", "낙담한", "남의 시선을 의식하는", "노여워하는",
    "눈물이 나는", "느긋", "당혹스러운", "당황", "두려운", "마비된",
    "만족스러운", "방어적인", "배신당한", "버려진", "부끄러운", "분노",
    "불안", "비통한", "상처", "성가신", "스트레스 받는", "슬픔",
    "신뢰하는", "신이 난", "실망한", "악의적인", "안달하는", "안도",
    "억울한", "열등감", "염세적인", "외로운", "우울한", "자신하는",
    "조심스러운", "좌절한", "죄책감의", "질투하는", "짜증내는", "초조한",
    "충격 받은", "취약한", "툴툴대는", "편안한", "한심한", "혐오스러운",
    "혼란스러운", "환멸을 느끼는", "회의적인", "후회되는", "흥분", "희생된"
]

# 대분류 → 소분류 매핑
CATEGORY_TO_DETAIL = {
    "분노": ["노여워하는", "분노", "성가신", "악의적인", "안달하는",
             "좌절한", "툴툴대는", "방어적인", "짜증내는", "구역질 나는"],
    "기쁨": ["느긋", "만족스러운", "신뢰하는", "신이 난", "안도",
             "자신하는", "기쁨", "흥분", "편안한", "감사하는"],
    "불안": ["걱정스러운", "당혹스러운", "불안", "스트레스 받는", "회의적인",
             "초조한", "취약한", "두려운", "조심스러운", "혼란스러운"],
    "당황": ["당황", "부끄러운", "열등감", "외로운", "고립된",
             "혐오스러운", "한심한", "남의 시선을 의식하는", "죄책감의", "혼란스러운"],
    "슬픔": ["마비된", "비통한", "슬픔", "실망한", "염세적인",
             "우울한", "후회되는", "눈물이 나는", "낙담한", "환멸을 느끼는"],
    "상처": ["배신당한", "버려진", "상처", "억울한", "충격 받은",
             "고립된", "질투하는", "희생된", "괴로워하는", "가난한, 불우한"],
}

CATEGORY2ID = {label: i for i, label in enumerate(CATEGORY_LABELS)}
ID2CATEGORY  = {i: label for label, i in CATEGORY2ID.items()}
DETAIL2ID    = {label: i for i, label in enumerate(DETAIL_LABELS)}
ID2DETAIL    = {i: label for label, i in DETAIL2ID.items()}

NUM_CATEGORY_LABELS = len(CATEGORY_LABELS)
NUM_DETAIL_LABELS   = len(DETAIL_LABELS)

# ── 현재 버전 확인 ─────────────────────────────────────────
if __name__ == "__main__":
    print(f"MODEL_VERSION:      {MODEL_VERSION}")
    print(f"MODEL_OUTPUT_DIR:   {MODEL_OUTPUT_DIR}")
    print(f"CATEGORY_WEIGHTS:   {CATEGORY_WEIGHTS}")
    print(f"DEFAULT_WEIGHT:     {DEFAULT_WEIGHT}")
    print(f"AUGMENT_MULTIPLIER: {AUGMENT_MULTIPLIER}")

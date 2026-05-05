# ============================================================
# stage1_prep.py — 전처리 + 샘플링 + 증강 파이프라인
# 실행: python data\finetune\stage1_prep.py
# 출력: data/processed/train_{VERSION}.pkl, val_{VERSION}.pkl
# ============================================================

import os
import random
import pickle
import pandas as pd
from collections import Counter
from typing import List, Dict

from config import (
    DATA_TRAIN_PATH, DATA_VAL_PATH,
    CATEGORY_WEIGHTS, DEFAULT_WEIGHT, CATEGORY_KEYWORD_MAP,
    CATEGORY2ID, DETAIL2ID,
    AUGMENT_TARGET_CATEGORY, AUGMENT_MULTIPLIER,
    MODEL_VERSION, SEED,
)
from augment import augment_dataframe_rows

random.seed(SEED)
PROCESSED_DIR = "data/processed"
os.makedirs(PROCESSED_DIR, exist_ok=True)


# ── 1. 엑셀 로딩 ──────────────────────────────────────────

def load_excel(path: str) -> pd.DataFrame:
    print(f"[load] {path}")
    df = pd.read_excel(path)
    print(f"  → {len(df)}행 로드 완료")
    return df


# ── 2. 컬럼 정리 ──────────────────────────────────────────

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = ["성별", "상황키워드", "감정_대분류", "감정_소분류",
            "사람문장1", "사람문장2", "사람문장3"]
    df = df[keep].copy()
    df["성별"]       = df["성별"].fillna("미상")
    df["상황키워드"] = df["상황키워드"].fillna("기타")
    df = df.dropna(subset=["감정_대분류", "감정_소분류", "사람문장1"])
    return df


# ── 3. 카테고리 분류 ───────────────────────────────────────

def get_category_group(situation: str) -> str:
    for group, keywords in CATEGORY_KEYWORD_MAP.items():
        for kw in keywords:
            if kw in situation:
                return group
    return "기타"


# ── 4. 문장 분리 → 샘플 목록 생성 ─────────────────────────

def expand_to_samples(df: pd.DataFrame) -> List[Dict]:
    samples = []
    for _, row in df.iterrows():
        gender    = str(row["성별"])
        situation = str(row["상황키워드"])
        cat_label = str(row["감정_대분류"])
        det_label = str(row["감정_소분류"])

        if cat_label not in CATEGORY2ID or det_label not in DETAIL2ID:
            continue

        cat_id = CATEGORY2ID[cat_label]
        det_id = DETAIL2ID[det_label]
        group  = get_category_group(situation)

        for col in ["사람문장1", "사람문장2", "사람문장3"]:
            sentence = row.get(col)
            if pd.isna(sentence) or str(sentence).strip() == "":
                continue
            text = f"[성별] {gender} [상황] {situation} [발화] {str(sentence).strip()}"
            samples.append({
                "text":           text,
                "category_label": cat_id,
                "detail_label":   det_id,
                "gender":         gender,
                "situation":      situation,
                "group":          group,
                "is_augmented":   False,
            })
    return samples


# ── 5. 샘플링 ─────────────────────────────────────────────

def apply_sampling(samples: List[Dict]) -> List[Dict]:
    grouped: Dict[str, List[Dict]] = {}
    for s in samples:
        grouped.setdefault(s["group"], []).append(s)

    result = []
    for group, group_samples in grouped.items():
        weight = CATEGORY_WEIGHTS.get(group, DEFAULT_WEIGHT)
        if weight >= 1.0:
            repeat  = int(weight)
            extra_n = int(len(group_samples) * (weight - repeat))
            result.extend(group_samples * repeat)
            result.extend(random.sample(group_samples, min(extra_n, len(group_samples))))
        else:
            keep_n = max(1, int(len(group_samples) * weight))
            result.extend(random.sample(group_samples, keep_n))

    print(f"[sampling] 샘플링 후 총 샘플: {len(result)}")
    for g, cnt in sorted(Counter(s["group"] for s in result).items()):
        print(f"  {g}: {cnt}")
    return result


# ── 6. 텍스트 증강 ────────────────────────────────────────

def apply_augmentation(samples: List[Dict]) -> List[Dict]:
    if AUGMENT_MULTIPLIER == 0:
        print("[augment] 증강 없음 (unweighted 모드)")
        return samples

    target    = [s for s in samples if s["group"] == AUGMENT_TARGET_CATEGORY]
    augmented = augment_dataframe_rows(target, multiplier=AUGMENT_MULTIPLIER)
    for a in augmented:
        a["group"] = AUGMENT_TARGET_CATEGORY

    result = samples + augmented
    print(f"[augment] 증강 추가 샘플: {len(augmented)}")
    print(f"[augment] 증강 후 총 샘플: {len(result)}")
    return result


# ── 7. 저장 ───────────────────────────────────────────────

def save_samples(samples: List[Dict], path: str):
    random.shuffle(samples)
    with open(path, "wb") as f:
        pickle.dump(samples, f)
    print(f"[save] {path} → {len(samples)}샘플 저장")


# ── 메인 ──────────────────────────────────────────────────

def run_prep(split: str = "train"):
    path = DATA_TRAIN_PATH if split == "train" else DATA_VAL_PATH
    # 버전명 포함한 pkl 파일명
    out  = os.path.join(PROCESSED_DIR, f"{split}_{MODEL_VERSION}.pkl")

    df      = load_excel(path)
    df      = clean_columns(df)
    samples = expand_to_samples(df)
    print(f"[expand] 문장 분리 후 샘플: {len(samples)}")

    if split == "train":
        samples = apply_sampling(samples)
        samples = apply_augmentation(samples)

    save_samples(samples, out)
    return out


if __name__ == "__main__":
    print("=" * 50)
    print(f"[버전] {MODEL_VERSION}")
    print("=" * 50)

    print("\nTraining 전처리 시작")
    run_prep("train")

    print("\nValidation 전처리 시작")
    run_prep("val")

    print("\n전처리 완료.")
    print(f"  train_{MODEL_VERSION}.pkl")
    print(f"  val_{MODEL_VERSION}.pkl")

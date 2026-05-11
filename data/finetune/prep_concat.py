# ============================================================
# prep_concat.py — 다중 발화 연결 전처리
#
# 사람문장1~3을 연결하여 맥락 풍부한 샘플 생성.
# 가중치/증강 없음.
#
# 실행: python data/finetune/prep_concat.py
# 출력: data/processed/train_concat.pkl, val_concat.pkl
# ============================================================

import os
import random
import pickle
import pandas as pd
from collections import Counter
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    DATA_TRAIN_PATH, DATA_VAL_PATH,
    CATEGORY2ID, DETAIL2ID, CATEGORY_KEYWORD_MAP,
    ID2CATEGORY, ID2DETAIL, SEED,
)

random.seed(SEED)
PROCESSED_DIR = "data/processed"
os.makedirs(PROCESSED_DIR, exist_ok=True)


def get_category_group(situation):
    for group, keywords in CATEGORY_KEYWORD_MAP.items():
        for kw in keywords:
            if kw in situation:
                return group
    return "기타"


def load_and_prep(excel_path):
    print(f"[load] {excel_path}")
    df = pd.read_excel(excel_path)

    keep = ["성별", "상황키워드", "감정_대분류", "감정_소분류",
            "사람문장1", "사람문장2", "사람문장3"]
    df = df[keep].copy()
    df["성별"] = df["성별"].fillna("미상")
    df["상황키워드"] = df["상황키워드"].fillna("기타")
    df = df.dropna(subset=["감정_대분류", "감정_소분류", "사람문장1"])

    samples = []
    for _, row in df.iterrows():
        cat_label = str(row["감정_대분류"])
        det_label = str(row["감정_소분류"])
        if cat_label not in CATEGORY2ID or det_label not in DETAIL2ID:
            continue

        gender = str(row["성별"])
        situation = str(row["상황키워드"])

        parts = []
        for col in ["사람문장1", "사람문장2", "사람문장3"]:
            s = row.get(col)
            if pd.notna(s) and str(s).strip():
                parts.append(str(s).strip())

        text = "[성별] " + gender + " [상황] " + situation + " [발화] " + " ".join(parts)

        samples.append({
            "text":           text,
            "category_label": CATEGORY2ID[cat_label],
            "detail_label":   DETAIL2ID[det_label],
            "gender":         gender,
            "situation":      situation,
            "group":          get_category_group(situation),
        })

    print(f"  -> {len(samples)}건")
    return samples


def print_dist(samples, label):
    cat_dist = Counter(ID2CATEGORY[s["category_label"]] for s in samples)
    det_dist = Counter(ID2DETAIL[s["detail_label"]] for s in samples)
    print(f"\n[{label}] 대분류 분포:")
    for k, v in sorted(cat_dist.items(), key=lambda x: -x[1]):
        pct = v / len(samples) * 100
        print(f"  {k}: {v:,} ({pct:.1f}%)")
    print(f"[{label}] 소분류 범위: {min(det_dist.values()):,} ~ {max(det_dist.values()):,}")


def save(samples, path):
    random.shuffle(samples)
    with open(path, "wb") as f:
        pickle.dump(samples, f)
    print(f"[save] {path} -> {len(samples):,}건")


if __name__ == "__main__":
    print("=" * 50)
    print("  다중 발화 연결 전처리 (concat)")
    print("=" * 50)

    train = load_and_prep(DATA_TRAIN_PATH)
    val = load_and_prep(DATA_VAL_PATH)

    print_dist(train, "train")
    print_dist(val, "val")

    save(train, os.path.join(PROCESSED_DIR, "train_concat.pkl"))
    save(val, os.path.join(PROCESSED_DIR, "val_concat.pkl"))

    print("\n전처리 완료.")

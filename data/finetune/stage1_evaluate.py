# ============================================================
# stage1_evaluate.py — 학습된 모델 상세 평가
# 실행: python stage1_evaluate.py
# 출력: 콘솔 + models/emotion_best/eval_report.json
# ============================================================

import os
import json
import pickle
import torch
import numpy as np
from torch.utils.data import DataLoader
from transformers import AutoTokenizer
from sklearn.metrics import (
    f1_score, classification_report, confusion_matrix
)
from tqdm import tqdm

from config import (
    BASE_MODEL_NAME, MAX_LENGTH, EVAL_BATCH_SIZE,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    CATEGORY_LABELS, DETAIL_LABELS,
    ID2CATEGORY, ID2DETAIL,
    MODEL_OUTPUT_DIR, SEED,
)
from stage1_train import MultiTaskEmotionModel, EmotionDataset

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_model(model_dir: str) -> tuple:
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model     = MultiTaskEmotionModel(
        BASE_MODEL_NAME,
        num_category=NUM_CATEGORY_LABELS,
        num_detail=NUM_DETAIL_LABELS,
    )
    state_dict = torch.load(
        os.path.join(model_dir, "best_model.pt"),
        map_location=DEVICE,
    )
    model.load_state_dict(state_dict)
    model.to(DEVICE)
    model.eval()
    return model, tokenizer


def run_evaluation(model, loader) -> dict:
    cat_preds, cat_labels = [], []
    det_preds, det_labels = [], []

    with torch.no_grad():
        for batch in tqdm(loader, desc="evaluating"):
            input_ids      = batch["input_ids"].to(DEVICE)
            attention_mask = batch["attention_mask"].to(DEVICE)
            token_type_ids = batch["token_type_ids"].to(DEVICE)

            cat_logits, det_logits = model(input_ids, attention_mask, token_type_ids)

            cat_preds.extend(torch.argmax(cat_logits, dim=-1).cpu().numpy())
            cat_labels.extend(batch["category_label"].numpy())
            det_preds.extend(torch.argmax(det_logits, dim=-1).cpu().numpy())
            det_labels.extend(batch["detail_label"].numpy())

    return {
        "cat_preds":  cat_preds,  "cat_labels":  cat_labels,
        "det_preds":  det_preds,  "det_labels":  det_labels,
    }


def print_report(results: dict):
    cat_preds  = results["cat_preds"]
    cat_labels = results["cat_labels"]
    det_preds  = results["det_preds"]
    det_labels = results["det_labels"]

    cat_f1 = f1_score(cat_labels, cat_preds, average="macro", zero_division=0)
    det_f1 = f1_score(det_labels, det_preds, average="macro", zero_division=0)

    print("\n" + "=" * 60)
    print("[ 대분류 (6클래스) 평가 결과 ]")
    print("=" * 60)
    print(f"  Macro F1: {cat_f1:.4f}  {'✅ 목표 달성' if cat_f1 >= 0.82 else '⚠️ 목표 미달'}")
    print()
    print(classification_report(
        cat_labels, cat_preds,
        target_names=CATEGORY_LABELS,
        zero_division=0,
    ))

    print("\n" + "=" * 60)
    print("[ 소분류 (58클래스) 평가 결과 ]")
    print("=" * 60)
    print(f"  Macro F1: {det_f1:.4f}  {'✅ 목표 달성' if det_f1 >= 0.73 else '⚠️ 목표 미달'}")
    print()
    # 소분류는 클래스가 많아서 상위/하위 10개만 출력
    per_class = f1_score(det_labels, det_preds, average=None, zero_division=0)
    label_f1  = sorted(
        [(DETAIL_LABELS[i], float(per_class[i])) for i in range(len(DETAIL_LABELS))],
        key=lambda x: x[1], reverse=True
    )
    print("  [상위 10개 소분류]")
    for name, score in label_f1[:10]:
        print(f"    {name}: {score:.4f}")
    print("  [하위 10개 소분류]")
    for name, score in label_f1[-10:]:
        print(f"    {name}: {score:.4f}")

    # 혼동행렬 (대분류)
    print("\n[ 대분류 혼동행렬 ]")
    cm = confusion_matrix(cat_labels, cat_preds)
    header = "      " + "  ".join(f"{l[:3]:>4}" for l in CATEGORY_LABELS)
    print(header)
    for i, row in enumerate(cm):
        row_str = "  ".join(f"{v:>4}" for v in row)
        print(f"  {CATEGORY_LABELS[i][:3]:>4}  {row_str}")

    return cat_f1, det_f1, label_f1


def main():
    with open("data/processed/val.pkl", "rb") as f:
        val_samples = pickle.load(f)

    model, tokenizer = load_model(MODEL_OUTPUT_DIR)
    val_dataset      = EmotionDataset(val_samples, tokenizer, MAX_LENGTH)
    val_loader       = DataLoader(val_dataset, batch_size=EVAL_BATCH_SIZE,
                                  shuffle=False, num_workers=2)

    results           = run_evaluation(model, val_loader)
    cat_f1, det_f1, label_f1 = print_report(results)

    # JSON 저장
    report = {
        "cat_f1_macro": round(cat_f1, 4),
        "det_f1_macro": round(det_f1, 4),
        "target_cat_f1": 0.82,
        "target_det_f1": 0.73,
        "cat_goal_met": cat_f1 >= 0.82,
        "det_goal_met": det_f1 >= 0.73,
        "detail_per_class_f1": {name: round(score, 4) for name, score in label_f1},
    }
    out_path = os.path.join(MODEL_OUTPUT_DIR, "eval_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[저장] {out_path}")


if __name__ == "__main__":
    main()

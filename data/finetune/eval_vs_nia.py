# ============================================================
# eval_vs_nia.py — NIA ALBERT vs 우리 KcELECTRA 동일 테스트셋 비교
#
# NIA 테스트 데이터(5,135건)로 우리 모델을 평가.
# 두 가지 입력 방식으로 테스트:
#   A) 사람 발화만 추출 (우리 모델 원래 방식)
#   B) 대화 전체 입력 (NIA 방식, 공정 비교용)
#
# 실행: python data/finetune/eval_vs_nia.py --nia-data path/to/NIA/data_text/test.txt
# ============================================================

import os
import pickle
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from collections import Counter
from transformers import AutoTokenizer, AutoModel
from sklearn.metrics import f1_score, accuracy_score, classification_report
from tqdm import tqdm
import argparse

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    BASE_MODEL_NAME, MAX_LENGTH,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    CATEGORY_LABELS,
)

NIA_DATA = None  # --nia-data 인자로 전달

# NIA label(0~5) → 우리 label 매핑
# NIA 순서: 0=분노, 1=기쁨, 2=불안, 3=당황, 4=슬픔, 5=상처
# 우리 순서: 0=분노, 1=기쁨, 2=불안, 3=당황, 4=슬픔, 5=상처 (동일)
NIA_TO_OURS = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class MultiTaskEmotionModel(nn.Module):
    def __init__(self, model_name, num_category, num_detail, dropout=0.1):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        self.dropout = nn.Dropout(dropout)
        self.category_head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_category),
        )
        self.detail_head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_detail),
        )

    def forward(self, input_ids, attention_mask, token_type_ids=None):
        out = self.encoder(input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids)
        cls = self.dropout(out.last_hidden_state[:, 0, :])
        return self.category_head(cls), self.detail_head(cls)


def load_model(model_path):
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_NAME)
    model = MultiTaskEmotionModel(BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS).to(DEVICE)
    model.load_state_dict(torch.load(os.path.join(model_path, "best_model.pt"), map_location=DEVICE))
    model.eval()
    return model, tokenizer


def load_nia_test(nia_data_path):
    samples = []
    with open(nia_data_path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue
            label = int(parts[0])
            full_text = parts[1]

            sents = full_text.split("+")
            person_sents = [s.strip() for i, s in enumerate(sents) if i % 2 == 0]

            samples.append({
                "label": NIA_TO_OURS[label],
                "full_text": full_text.replace("+", " "),
                "person_sents": person_sents,
            })
    return samples


def predict_batch(model, tokenizer, texts, batch_size=64):
    all_preds = []
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i+batch_size]
        enc = tokenizer(batch_texts, truncation=True, padding=True, max_length=MAX_LENGTH, return_tensors="pt")
        enc = {k: v.to(DEVICE) for k, v in enc.items()}
        with torch.no_grad():
            cat_logits, _ = model(**enc)
            preds = torch.argmax(cat_logits, dim=-1).cpu().numpy()
            all_preds.extend(preds)
    return np.array(all_preds)


def evaluate(labels, preds, desc):
    acc = accuracy_score(labels, preds)
    macro_f1 = f1_score(labels, preds, average="macro")
    weighted_f1 = f1_score(labels, preds, average="weighted")

    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    print(f"  Accuracy:    {acc:.4f} ({acc*100:.1f}%)")
    print(f"  Macro F1:    {macro_f1:.4f}")
    print(f"  Weighted F1: {weighted_f1:.4f}")
    print(f"\n  Classification Report:")
    print(classification_report(
        labels, preds,
        target_names=CATEGORY_LABELS,
        digits=4,
    ))
    return acc, macro_f1


def main():
    parser = argparse.ArgumentParser(description="NIA ALBERT vs KcELECTRA 비교 평가")
    parser.add_argument("--nia-data", required=True,
                        help="NIA 테스트 데이터 경로 (data_text/test.txt)")
    parser.add_argument("--models", nargs="+",
                        default=["models/unweighted", "models/unweighted_aigen", "models/concat_unweight"],
                        help="평가할 모델 경로 목록")
    args = parser.parse_args()

    print(f"[device] {DEVICE}")

    samples = load_nia_test(args.nia_data)
    print(f"[data] NIA test: {len(samples)}건")

    labels = np.array([s["label"] for s in samples])
    dist = Counter(labels)
    for idx, name in enumerate(CATEGORY_LABELS):
        print(f"  {name}: {dist.get(idx, 0)}")

    models_to_eval = {}
    for p in args.models:
        name = Path(p).name
        models_to_eval[name] = p

    for model_name, model_path in models_to_eval.items():
        if not os.path.exists(os.path.join(model_path, "best_model.pt")):
            print(f"\n[skip] {model_name} ({model_path}) - best_model.pt 없음")
            continue

        print(f"\n{'#'*60}")
        print(f"  Loading: {model_name}")
        print(f"{'#'*60}")
        model, tokenizer = load_model(model_path)

        # A) 사람 발화만 (발화1만 사용)
        texts_person1 = [s["person_sents"][0] for s in samples]
        preds_a = predict_batch(model, tokenizer, texts_person1)
        evaluate(labels, preds_a, f"{model_name} | 사람 발화1만")

        # B) 사람 발화 전체 연결
        texts_person_all = [" ".join(s["person_sents"]) for s in samples]
        preds_b = predict_batch(model, tokenizer, texts_person_all)
        evaluate(labels, preds_b, f"{model_name} | 사람 발화 전체 연결")

        # C) 대화 전체 (NIA 방식)
        texts_full = [s["full_text"] for s in samples]
        preds_c = predict_batch(model, tokenizer, texts_full)
        evaluate(labels, preds_c, f"{model_name} | 대화 전체 (NIA 방식)")

        del model
        torch.cuda.empty_cache()

    # NIA 보고 수치
    print(f"\n{'#'*60}")
    print(f"  NIA ALBERT (보고 수치)")
    print(f"{'#'*60}")
    print(f"  Accuracy:  71.6%")
    print(f"  F1 Score:  0.835 (binary, threshold=0.5)")
    print(f"  Precision: 0.717")
    print(f"  Recall:    0.999")
    print(f"  (주의: macro F1이 아닌 threshold 기반 binary F1)")


if __name__ == "__main__":
    main()

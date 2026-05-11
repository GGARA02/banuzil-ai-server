# ============================================================
# stage1_train_concat.py — 다중 발화 연결 학습
#
# 기존: 사람문장1, 2, 3을 각각 개별 샘플로 분리
# 변경: 사람문장1~3을 하나로 연결 → 맥락 풍부한 학습
#
# 데이터: 원본 엑셀에서 직접 전처리 (가중치 없음)
#
# 실행: python data/finetune/stage1_train_concat.py
#       python data/finetune/stage1_train_concat.py --epochs 3
# ============================================================

import os
import json
import pickle
import random
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from collections import Counter
from torch.utils.data import Dataset, DataLoader
from transformers import (
    AutoTokenizer, AutoModel, get_linear_schedule_with_warmup,
)
from sklearn.metrics import f1_score
from tqdm import tqdm
import argparse

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    BASE_MODEL_NAME, MAX_LENGTH, NUM_EPOCHS,
    TRAIN_BATCH_SIZE, EVAL_BATCH_SIZE,
    LEARNING_RATE, WARMUP_RATIO, WEIGHT_DECAY,
    LOSS_WEIGHT_CATEGORY, LOSS_WEIGHT_DETAIL,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    CATEGORY2ID, DETAIL2ID, CATEGORY_KEYWORD_MAP,
    ID2CATEGORY, ID2DETAIL,
    DATA_TRAIN_PATH, DATA_VAL_PATH, SEED,
)

OUTPUT_DIR = "models/concat"
CHECKPOINT_DIR = "models/concat_checkpoints"

random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ── 전처리: 문장 연결 방식 ────────────────────────────────────

def get_category_group(situation):
    for group, keywords in CATEGORY_KEYWORD_MAP.items():
        for kw in keywords:
            if kw in situation:
                return group
    return "기타"


def load_and_prep(excel_path):
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

    return samples


# ── Dataset ───────────────────────────────────────────────────

class EmotionDataset(Dataset):
    def __init__(self, samples, tokenizer, max_length):
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        enc = self.tokenizer(
            s["text"], truncation=True, padding="max_length",
            max_length=self.max_length, return_tensors="pt",
        )
        return {
            "input_ids":      enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "token_type_ids": enc.get("token_type_ids",
                              torch.zeros(self.max_length, dtype=torch.long)).squeeze(0),
            "category_label": torch.tensor(s["category_label"], dtype=torch.long),
            "detail_label":   torch.tensor(s["detail_label"],   dtype=torch.long),
        }


# ── Multi-task 모델 ───────────────────────────────────────────

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


# ── 학습/평가 루프 ────────────────────────────────────────────

def train_epoch(model, loader, optimizer, scheduler, criterion):
    model.train()
    total_loss = 0.0
    cat_p, cat_l, det_p, det_l = [], [], [], []

    for batch in tqdm(loader, desc="  train", leave=False):
        ids  = batch["input_ids"].to(DEVICE)
        mask = batch["attention_mask"].to(DEVICE)
        tt   = batch["token_type_ids"].to(DEVICE)
        cl   = batch["category_label"].to(DEVICE)
        dl   = batch["detail_label"].to(DEVICE)

        optimizer.zero_grad()
        clo, dlo = model(ids, mask, tt)
        loss = LOSS_WEIGHT_CATEGORY * criterion(clo, cl) + \
               LOSS_WEIGHT_DETAIL   * criterion(dlo, dl)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()

        total_loss += loss.item()
        cat_p.extend(torch.argmax(clo, -1).cpu().numpy())
        cat_l.extend(cl.cpu().numpy())
        det_p.extend(torch.argmax(dlo, -1).cpu().numpy())
        det_l.extend(dl.cpu().numpy())

    return (total_loss / len(loader),
            f1_score(cat_l, cat_p, average="macro", zero_division=0),
            f1_score(det_l, det_p, average="macro", zero_division=0))


def eval_epoch(model, loader, criterion):
    model.eval()
    total_loss = 0.0
    cat_p, cat_l, det_p, det_l = [], [], [], []

    with torch.no_grad():
        for batch in tqdm(loader, desc="  eval ", leave=False):
            ids  = batch["input_ids"].to(DEVICE)
            mask = batch["attention_mask"].to(DEVICE)
            tt   = batch["token_type_ids"].to(DEVICE)
            cl   = batch["category_label"].to(DEVICE)
            dl   = batch["detail_label"].to(DEVICE)

            clo, dlo = model(ids, mask, tt)
            loss = LOSS_WEIGHT_CATEGORY * criterion(clo, cl) + \
                   LOSS_WEIGHT_DETAIL   * criterion(dlo, dl)
            total_loss += loss.item()
            cat_p.extend(torch.argmax(clo, -1).cpu().numpy())
            cat_l.extend(cl.cpu().numpy())
            det_p.extend(torch.argmax(dlo, -1).cpu().numpy())
            det_l.extend(dl.cpu().numpy())

    return (total_loss / len(loader),
            f1_score(cat_l, cat_p, average="macro", zero_division=0),
            f1_score(det_l, det_p, average="macro", zero_division=0))


# ── 메인 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=NUM_EPOCHS)
    parser.add_argument("--batch", type=int, default=TRAIN_BATCH_SIZE)
    parser.add_argument("--lr", type=float, default=LEARNING_RATE)
    args = parser.parse_args()

    print(f"[device] {DEVICE}")
    print(f"[config] epochs={args.epochs}, batch={args.batch}, lr={args.lr}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

    # 엑셀에서 직접 전처리 (문장 연결 방식)
    train_samples = load_and_prep(DATA_TRAIN_PATH)
    val_samples = load_and_prep(DATA_VAL_PATH)

    random.shuffle(train_samples)

    print(f"[data] train: {len(train_samples):,}개 (문장 연결)")
    print(f"[data] val:   {len(val_samples):,}개")

    cat_dist = Counter(ID2CATEGORY[s["category_label"]] for s in train_samples)
    print(f"[분포] 대분류:")
    for k, v in sorted(cat_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v:,} ({v/len(train_samples)*100:.1f}%)")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_NAME)
    train_dataset = EmotionDataset(train_samples, tokenizer, MAX_LENGTH)
    val_dataset = EmotionDataset(val_samples, tokenizer, MAX_LENGTH)
    train_loader = DataLoader(train_dataset, batch_size=args.batch,
                              shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=EVAL_BATCH_SIZE,
                            shuffle=False, num_workers=4, pin_memory=True)

    model = MultiTaskEmotionModel(
        BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
    ).to(DEVICE)

    optimizer = torch.optim.AdamW([
        {"params": model.encoder.parameters(),       "lr": args.lr},
        {"params": model.category_head.parameters(), "lr": args.lr * 5},
        {"params": model.detail_head.parameters(),   "lr": args.lr * 5},
    ], weight_decay=WEIGHT_DECAY)

    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * WARMUP_RATIO)
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    criterion = nn.CrossEntropyLoss()

    best_cat_f1 = 0.0
    history = []

    print(f"\n[train] 학습 시작 ({args.epochs} epochs, {len(train_samples):,} samples)")
    print(f"  total steps: {total_steps:,}, warmup: {warmup_steps:,}")
    print("-" * 60)

    for epoch in range(1, args.epochs + 1):
        print(f"\nEpoch {epoch}/{args.epochs}")

        tr_loss, tr_cat_f1, tr_det_f1 = train_epoch(
            model, train_loader, optimizer, scheduler, criterion)
        val_loss, val_cat_f1, val_det_f1 = eval_epoch(
            model, val_loader, criterion)

        print(f"  train -> loss: {tr_loss:.4f} | cat_f1: {tr_cat_f1:.4f} | det_f1: {tr_det_f1:.4f}")
        print(f"  val   -> loss: {val_loss:.4f} | cat_f1: {val_cat_f1:.4f} | det_f1: {val_det_f1:.4f}")

        history.append({
            "epoch": epoch,
            "tr_loss": round(tr_loss, 4), "tr_cat_f1": round(tr_cat_f1, 4), "tr_det_f1": round(tr_det_f1, 4),
            "val_loss": round(val_loss, 4), "val_cat_f1": round(val_cat_f1, 4), "val_det_f1": round(val_det_f1, 4),
        })

        ckpt_path = os.path.join(CHECKPOINT_DIR, f"epoch{epoch}")
        os.makedirs(ckpt_path, exist_ok=True)
        torch.save(model.state_dict(), os.path.join(ckpt_path, "model.pt"))
        tokenizer.save_pretrained(ckpt_path)

        if val_cat_f1 > best_cat_f1:
            best_cat_f1 = val_cat_f1
            torch.save(model.state_dict(), os.path.join(OUTPUT_DIR, "best_model.pt"))
            tokenizer.save_pretrained(OUTPUT_DIR)
            model.encoder.config.save_pretrained(OUTPUT_DIR)
            print(f"  * best model saved (cat_f1: {best_cat_f1:.4f})")

    with open(os.path.join(OUTPUT_DIR, "train_history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n[done] best val cat_f1: {best_cat_f1:.4f}")
    print(f"[done] model path: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

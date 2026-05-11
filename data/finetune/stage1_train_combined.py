# ============================================================
# stage1_train_combined.py — 기존 데이터 + AI 생성 데이터 통합 학습
#
# 가중치 없이 아래 데이터를 합쳐서 파인튜닝:
#   1) data/processed/train_unweighted.pkl   (기존 학습 데이터)
#   2) data/processed/ai_generated_*.pkl     (균등 생성)
#   3) data/processed/ai_fill_*.pkl          (부족 소분류 보충)
#
# 실행: python data/finetune/stage1_train_combined.py
#       python data/finetune/stage1_train_combined.py --epochs 3
# ============================================================

import os
import glob
import json
import pickle
import random
import numpy as np
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
    BASE_MODEL_NAME,
    MAX_LENGTH, NUM_EPOCHS,
    TRAIN_BATCH_SIZE, EVAL_BATCH_SIZE,
    LEARNING_RATE, WARMUP_RATIO, WEIGHT_DECAY,
    LOSS_WEIGHT_CATEGORY, LOSS_WEIGHT_DETAIL,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    ID2CATEGORY, ID2DETAIL, SEED,
)

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "processed"
OUTPUT_DIR = "models/unweighted_aigen"
CHECKPOINT_DIR = "models/unweighted_aigen_checkpoints"

random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


# ── 데이터 로딩 ──────────────────────────────────────────────

def load_all_data():
    train_pkl = PROCESSED_DIR / "train_unweighted.pkl"
    val_pkl = PROCESSED_DIR / "val_unweighted.pkl"

    with open(train_pkl, "rb") as f:
        train_samples = pickle.load(f)
    with open(val_pkl, "rb") as f:
        val_samples = pickle.load(f)

    print(f"[기존] train: {len(train_samples):,}개")
    print(f"[기존] val:   {len(val_samples):,}개")

    ai_count = 0
    for pattern in ["ai_generated_*.pkl", "ai_fill_*.pkl"]:
        for path in sorted(PROCESSED_DIR.glob(pattern)):
            with open(path, "rb") as f:
                ai_data = pickle.load(f)
            train_samples.extend(ai_data)
            ai_count += len(ai_data)
            print(f"[AI]   {path.name}: {len(ai_data):,}개")

    print(f"\n[합계] train: {len(train_samples):,}개 (기존 + AI {ai_count:,}개)")
    print(f"[합계] val:   {len(val_samples):,}개 (변경 없음)")

    # 분포 확인
    cat_dist = Counter(ID2CATEGORY[s["category_label"]] for s in train_samples)
    print(f"\n[분포] 대분류:")
    for k, v in sorted(cat_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v:,} ({v/len(train_samples)*100:.1f}%)")

    det_dist = Counter(ID2DETAIL[s["detail_label"]] for s in train_samples)
    min_det = min(det_dist.values())
    max_det = max(det_dist.values())
    min_name = [k for k, v in det_dist.items() if v == min_det][0]
    print(f"\n[분포] 소분류 범위: {min_name}({min_det:,}) ~ {max_det:,} (비율 {max_det/min_det:.1f}배)")

    random.shuffle(train_samples)
    return train_samples, val_samples


# ── Dataset ───────────────────────────────────────────────────

class EmotionDataset(Dataset):
    def __init__(self, samples: list, tokenizer, max_length: int):
        self.samples = samples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s = self.samples[idx]
        enc = self.tokenizer(
            s["text"],
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
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
    def __init__(self, model_name: str,
                 num_category: int, num_detail: int,
                 dropout: float = 0.1):
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
        out = self.encoder(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
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

    train_samples, val_samples = load_all_data()

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
            torch.save(model.state_dict(),
                       os.path.join(OUTPUT_DIR, "best_model.pt"))
            tokenizer.save_pretrained(OUTPUT_DIR)
            model.encoder.config.save_pretrained(OUTPUT_DIR)
            print(f"  * best model saved (cat_f1: {best_cat_f1:.4f})")

    with open(os.path.join(OUTPUT_DIR, "train_history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n[done] best val cat_f1: {best_cat_f1:.4f}")
    print(f"[done] model path: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

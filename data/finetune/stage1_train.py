# ============================================================
# stage1_train.py — KoELECTRA Multi-task 파인튜닝 실행
# 실행: python data\finetune\stage1_train.py
# 사전 조건: stage1_prep.py 실행 완료
# ============================================================

import os
import json
import pickle
import random
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from transformers import (
    AutoTokenizer, AutoModel, get_linear_schedule_with_warmup,
)
from sklearn.metrics import f1_score
from tqdm import tqdm

from config import (
    BASE_MODEL_NAME,
    MAX_LENGTH, NUM_EPOCHS,
    TRAIN_BATCH_SIZE, EVAL_BATCH_SIZE,
    LEARNING_RATE, WARMUP_RATIO, WEIGHT_DECAY,
    LOSS_WEIGHT_CATEGORY, LOSS_WEIGHT_DETAIL,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    MODEL_OUTPUT_DIR, CHECKPOINT_DIR,
    MODEL_VERSION, SEED,
)

# ── 재현성 고정 ────────────────────────────────────────────
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[device] {DEVICE}")
print(f"[version] {MODEL_VERSION}")

os.makedirs(MODEL_OUTPUT_DIR, exist_ok=True)
os.makedirs(CHECKPOINT_DIR,   exist_ok=True)


# ── Dataset ────────────────────────────────────────────────

class EmotionDataset(Dataset):
    def __init__(self, samples: list, tokenizer, max_length: int):
        self.samples   = samples
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s   = self.samples[idx]
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


# ── Multi-task 모델 ────────────────────────────────────────

class MultiTaskEmotionModel(nn.Module):
    def __init__(self, model_name: str,
                 num_category: int, num_detail: int,
                 dropout: float = 0.1):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size  = self.encoder.config.hidden_size

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


# ── 학습 루프 ──────────────────────────────────────────────

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


# ── 메인 실행 ──────────────────────────────────────────────

def main():
    # 버전별 pkl 파일 로딩
    train_pkl = f"data/processed/train_{MODEL_VERSION}.pkl"
    val_pkl   = f"data/processed/val_{MODEL_VERSION}.pkl"

    with open(train_pkl, "rb") as f:
        train_samples = pickle.load(f)
    with open(val_pkl, "rb") as f:
        val_samples = pickle.load(f)

    print(f"[data] train: {len(train_samples)}, val: {len(val_samples)}")

    tokenizer     = AutoTokenizer.from_pretrained(BASE_MODEL_NAME)
    train_dataset = EmotionDataset(train_samples, tokenizer, MAX_LENGTH)
    val_dataset   = EmotionDataset(val_samples,   tokenizer, MAX_LENGTH)
    train_loader  = DataLoader(train_dataset, batch_size=TRAIN_BATCH_SIZE,
                               shuffle=True,  num_workers=4, pin_memory=True)
    val_loader    = DataLoader(val_dataset,   batch_size=EVAL_BATCH_SIZE,
                               shuffle=False, num_workers=4, pin_memory=True)

    model = MultiTaskEmotionModel(
        BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
    ).to(DEVICE)

    optimizer = torch.optim.AdamW([
        {"params": model.encoder.parameters(),       "lr": LEARNING_RATE},
        {"params": model.category_head.parameters(), "lr": LEARNING_RATE * 5},
        {"params": model.detail_head.parameters(),   "lr": LEARNING_RATE * 5},
    ], weight_decay=WEIGHT_DECAY)

    total_steps  = len(train_loader) * NUM_EPOCHS
    warmup_steps = int(total_steps * WARMUP_RATIO)
    scheduler    = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    criterion    = nn.CrossEntropyLoss()

    best_cat_f1 = 0.0
    history     = []

    print(f"\n[train] 학습 시작 — {NUM_EPOCHS} epochs")
    print(f"  total steps: {total_steps}, warmup steps: {warmup_steps}")
    print("-" * 60)

    for epoch in range(1, NUM_EPOCHS + 1):
        print(f"\nEpoch {epoch}/{NUM_EPOCHS}")

        tr_loss, tr_cat_f1, tr_det_f1 = train_epoch(
            model, train_loader, optimizer, scheduler, criterion)
        val_loss, val_cat_f1, val_det_f1 = eval_epoch(
            model, val_loader, criterion)

        print(f"  train → loss: {tr_loss:.4f} | cat_f1: {tr_cat_f1:.4f} | det_f1: {tr_det_f1:.4f}")
        print(f"  val   → loss: {val_loss:.4f} | cat_f1: {val_cat_f1:.4f} | det_f1: {val_det_f1:.4f}")

        history.append({
            "epoch": epoch,
            "tr_loss": round(tr_loss, 4), "tr_cat_f1": round(tr_cat_f1, 4), "tr_det_f1": round(tr_det_f1, 4),
            "val_loss": round(val_loss, 4), "val_cat_f1": round(val_cat_f1, 4), "val_det_f1": round(val_det_f1, 4),
        })

        # 체크포인트 저장
        ckpt_path = os.path.join(CHECKPOINT_DIR, f"epoch{epoch}")
        os.makedirs(ckpt_path, exist_ok=True)
        torch.save(model.state_dict(), os.path.join(ckpt_path, "model.pt"))
        tokenizer.save_pretrained(ckpt_path)

        # best 모델 저장
        if val_cat_f1 > best_cat_f1:
            best_cat_f1 = val_cat_f1
            torch.save(model.state_dict(),
                       os.path.join(MODEL_OUTPUT_DIR, "best_model.pt"))
            tokenizer.save_pretrained(MODEL_OUTPUT_DIR)
            model.encoder.config.save_pretrained(MODEL_OUTPUT_DIR)
            print(f"  ★ best model 저장 (cat_f1: {best_cat_f1:.4f})")

    with open(os.path.join(MODEL_OUTPUT_DIR, "train_history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n[완료] best val cat_f1: {best_cat_f1:.4f}")
    print(f"[완료] 모델 저장 경로: {MODEL_OUTPUT_DIR}")
    if best_cat_f1 >= 0.82:
        print("✅ 대분류 F1 목표(0.82) 달성")
    else:
        print(f"⚠️  대분류 F1 목표 미달 (현재 {best_cat_f1:.4f})")


if __name__ == "__main__":
    main()

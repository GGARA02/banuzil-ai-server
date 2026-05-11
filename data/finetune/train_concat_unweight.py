# ============================================================
# train_concat_unweight.py — 계층적 감성 분류 모델 학습
#
# 구조: 대분류 먼저 예측 → 해당 대분류의 소분류만 후보로 예측
#
#   CLS → Category Head → 대분류 (6개)
#            ↓ (대분류 임베딩)
#   CLS + 대분류 임베딩 → Detail Head → 소분류 (58개, 마스킹)
#
# 학습 시: ground truth 대분류로 마스킹 (teacher forcing)
# 추론 시: 예측된 대분류로 마스킹
#
# 사전 조건: prep_concat.py 실행 완료
# 실행: python data/finetune/train_concat_unweight.py
#       python data/finetune/train_concat_unweight.py --epochs 7
# ============================================================

import os
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
    BASE_MODEL_NAME, MAX_LENGTH, NUM_EPOCHS,
    TRAIN_BATCH_SIZE, EVAL_BATCH_SIZE,
    LEARNING_RATE, WARMUP_RATIO, WEIGHT_DECAY,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    CATEGORY_TO_DETAIL, CATEGORY2ID, DETAIL2ID,
    ID2CATEGORY, ID2DETAIL, SEED,
)

OUTPUT_DIR = "models/concat_unweight"
CHECKPOINT_DIR = "models/concat_unweight_checkpoints"
PROCESSED_DIR = Path("data/processed")

random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ── 대분류 → 소분류 ID 매핑 (마스킹용) ───────────────────────

CAT_TO_DETAIL_IDS = {}
for cat_name, detail_names in CATEGORY_TO_DETAIL.items():
    cat_id = CATEGORY2ID[cat_name]
    CAT_TO_DETAIL_IDS[cat_id] = [DETAIL2ID[d] for d in detail_names]


def build_detail_mask(category_ids, num_details):
    batch_size = len(category_ids)
    mask = torch.full((batch_size, num_details), float("-inf"))
    for i, cat_id in enumerate(category_ids):
        valid = CAT_TO_DETAIL_IDS[cat_id if isinstance(cat_id, int) else cat_id.item()]
        for d in valid:
            mask[i, d] = 0.0
    return mask


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


# ── 계층적 감성 분류 모델 ─────────────────────────────────────

class HierarchicalEmotionModel(nn.Module):
    def __init__(self, model_name, num_category, num_detail, dropout=0.1):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size = self.encoder.config.hidden_size
        self.num_detail = num_detail

        self.dropout = nn.Dropout(dropout)

        # 대분류 헤드
        self.category_head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_category),
        )

        # 대분류 임베딩 → 소분류 헤드에 주입
        self.category_embedding = nn.Embedding(num_category, hidden_size // 4)

        # 소분류 헤드 (CLS + 대분류 임베딩)
        detail_input_size = hidden_size + hidden_size // 4
        self.detail_head = nn.Sequential(
            nn.Linear(detail_input_size, hidden_size // 2),
            nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_detail),
        )

    def forward(self, input_ids, attention_mask, token_type_ids=None,
                category_ids=None):
        """
        category_ids: 학습 시 ground truth, 추론 시 None (자동 예측).
        """
        out = self.encoder(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        cls = self.dropout(out.last_hidden_state[:, 0, :])

        # 1단계: 대분류 예측
        cat_logits = self.category_head(cls)

        # 대분류 결정 (학습: GT, 추론: argmax)
        if category_ids is None:
            category_ids = torch.argmax(cat_logits, dim=-1)

        # 2단계: 대분류 임베딩 + CLS → 소분류 예측
        cat_emb = self.category_embedding(category_ids)
        detail_input = torch.cat([cls, cat_emb], dim=-1)
        detail_logits = self.detail_head(detail_input)

        # 해당 대분류의 소분류만 활성화 (마스킹)
        mask = build_detail_mask(category_ids, self.num_detail).to(detail_logits.device)
        detail_logits = detail_logits + mask

        return cat_logits, detail_logits


# ── 학습/평가 루프 ────────────────────────────────────────────

def train_epoch(model, loader, optimizer, scheduler, cat_criterion, det_criterion):
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

        # 학습: ground truth 대분류 전달 (teacher forcing)
        cat_logits, det_logits = model(ids, mask, tt, category_ids=cl)

        loss = 0.6 * cat_criterion(cat_logits, cl) + 0.4 * det_criterion(det_logits, dl)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()

        total_loss += loss.item()
        cat_p.extend(torch.argmax(cat_logits, -1).cpu().numpy())
        cat_l.extend(cl.cpu().numpy())
        det_p.extend(torch.argmax(det_logits, -1).cpu().numpy())
        det_l.extend(dl.cpu().numpy())

    return (total_loss / len(loader),
            f1_score(cat_l, cat_p, average="macro", zero_division=0),
            f1_score(det_l, det_p, average="macro", zero_division=0))


def eval_epoch(model, loader, cat_criterion, det_criterion):
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

            # loss: GT 대분류로 마스킹 (inf 방지)
            _, det_logits_gt = model(ids, mask, tt, category_ids=cl)
            cat_logits, det_logits_pred = model(ids, mask, tt, category_ids=None)

            loss = 0.6 * cat_criterion(cat_logits, cl) + 0.4 * det_criterion(det_logits_gt, dl)
            total_loss += loss.item()
            # F1: 예측 대분류로 마스킹 (실전과 동일)
            cat_p.extend(torch.argmax(cat_logits, -1).cpu().numpy())
            cat_l.extend(cl.cpu().numpy())
            det_p.extend(torch.argmax(det_logits_pred, -1).cpu().numpy())
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
    print(f"[model] HierarchicalEmotionModel (concat_unweight)")
    print(f"[config] epochs={args.epochs}, batch={args.batch}, lr={args.lr}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)

    # 전처리된 concat pkl 로드
    train_pkl = PROCESSED_DIR / "train_concat.pkl"
    val_pkl = PROCESSED_DIR / "val_concat.pkl"

    if not train_pkl.exists():
        print("[error] train_concat.pkl 없음. prep_concat.py를 먼저 실행하세요.")
        return

    with open(train_pkl, "rb") as f:
        train_samples = pickle.load(f)
    with open(val_pkl, "rb") as f:
        val_samples = pickle.load(f)

    print(f"[data] train: {len(train_samples):,}개")
    print(f"[data] val:   {len(val_samples):,}개")

    cat_dist = Counter(ID2CATEGORY[s["category_label"]] for s in train_samples)
    print(f"[분포] 대분류:")
    for k, v in sorted(cat_dist.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v:,} ({v/len(train_samples)*100:.1f}%)")

    # 계층 매핑 확인
    print(f"\n[계층] 대분류 -> 소분류 매핑:")
    for cat_id, det_ids in sorted(CAT_TO_DETAIL_IDS.items()):
        cat_name = ID2CATEGORY[cat_id]
        det_names = [ID2DETAIL[d] for d in det_ids]
        print(f"  {cat_name}: {det_names}")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_NAME)
    train_dataset = EmotionDataset(train_samples, tokenizer, MAX_LENGTH)
    val_dataset = EmotionDataset(val_samples, tokenizer, MAX_LENGTH)
    train_loader = DataLoader(train_dataset, batch_size=args.batch,
                              shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=EVAL_BATCH_SIZE,
                            shuffle=False, num_workers=4, pin_memory=True)

    model = HierarchicalEmotionModel(
        BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
    ).to(DEVICE)

    optimizer = torch.optim.AdamW([
        {"params": model.encoder.parameters(),            "lr": args.lr},
        {"params": model.category_head.parameters(),      "lr": args.lr * 5},
        {"params": model.category_embedding.parameters(), "lr": args.lr * 5},
        {"params": model.detail_head.parameters(),        "lr": args.lr * 5},
    ], weight_decay=WEIGHT_DECAY)

    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * WARMUP_RATIO)
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    cat_criterion = nn.CrossEntropyLoss()
    det_criterion = nn.CrossEntropyLoss()

    best_cat_f1 = 0.0
    best_det_f1 = 0.0
    history = []

    print(f"\n[train] 학습 시작 ({args.epochs} epochs, {len(train_samples):,} samples)")
    print(f"  total steps: {total_steps:,}, warmup: {warmup_steps:,}")
    print(f"  학습: teacher forcing (GT 대분류로 마스킹)")
    print(f"  평가: 예측 대분류로 마스킹 (실전 동일)")
    print("-" * 60)

    for epoch in range(1, args.epochs + 1):
        print(f"\nEpoch {epoch}/{args.epochs}")

        tr_loss, tr_cat_f1, tr_det_f1 = train_epoch(
            model, train_loader, optimizer, scheduler, cat_criterion, det_criterion)
        val_loss, val_cat_f1, val_det_f1 = eval_epoch(
            model, val_loader, cat_criterion, det_criterion)

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

        saved = False
        if val_cat_f1 > best_cat_f1:
            best_cat_f1 = val_cat_f1
            saved = True
        if val_det_f1 > best_det_f1:
            best_det_f1 = val_det_f1
            saved = True

        if saved:
            torch.save(model.state_dict(), os.path.join(OUTPUT_DIR, "best_model.pt"))
            tokenizer.save_pretrained(OUTPUT_DIR)
            model.encoder.config.save_pretrained(OUTPUT_DIR)
            print(f"  * best model saved (cat_f1: {best_cat_f1:.4f}, det_f1: {best_det_f1:.4f})")

    # 모델 메타 저장 (추론 시 계층 구조 복원용)
    meta = {
        "model_type": "hierarchical",
        "cat_to_detail_ids": {str(k): v for k, v in CAT_TO_DETAIL_IDS.items()},
        "num_category": NUM_CATEGORY_LABELS,
        "num_detail": NUM_DETAIL_LABELS,
        "base_model": BASE_MODEL_NAME,
        "max_length": MAX_LENGTH,
    }
    with open(os.path.join(OUTPUT_DIR, "model_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    with open(os.path.join(OUTPUT_DIR, "train_history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n[done] best val cat_f1: {best_cat_f1:.4f} | det_f1: {best_det_f1:.4f}")
    print(f"[done] model path: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

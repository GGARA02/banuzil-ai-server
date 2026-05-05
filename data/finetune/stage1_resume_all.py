# ============================================================
# stage1_resume_all.py — 3버전 순차 resume 학습
# 실행: python data\finetune\stage1_resume_all.py
# weighted → weighted_v2
# low_weight → low_weight_v2
# unweighted → unweighted_v2
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
    MAX_LENGTH, TRAIN_BATCH_SIZE, EVAL_BATCH_SIZE,
    LOSS_WEIGHT_CATEGORY, LOSS_WEIGHT_DETAIL,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    WEIGHT_DECAY, SEED,
)

# ============================================================
# ★ 여기만 수정하면 됨
# ============================================================
RESUME_PLAN = [
    {
        "resume_from": "weighted",
        "save_as":     "weighted_v2",
        "add_epochs":  2,
        "resume_lr":   5e-6,
    },
    {
        "resume_from": "low_weight",
        "save_as":     "low_weight_v2",
        "add_epochs":  2,
        "resume_lr":   5e-6,
    },
    {
        "resume_from": "unweighted",
        "save_as":     "unweighted_v2",
        "add_epochs":  2,
        "resume_lr":   5e-6,
    },
]
# ============================================================

random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(SEED)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[device] {DEVICE}")
print(f"[plan] 총 {len(RESUME_PLAN)}개 버전 순차 학습")
for p in RESUME_PLAN:
    print(f"  {p['resume_from']} → {p['save_as']} (+{p['add_epochs']} epochs, lr={p['resume_lr']})")
print()


# ── Dataset ────────────────────────────────────────────────

class EmotionDataset(Dataset):
    def __init__(self, samples, tokenizer, max_length):
        self.samples    = samples
        self.tokenizer  = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s   = self.samples[idx]
        enc = self.tokenizer(
            s["text"], truncation=True,
            padding="max_length", max_length=self.max_length,
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


# ── 모델 ───────────────────────────────────────────────────

class MultiTaskEmotionModel(nn.Module):
    def __init__(self, model_name, num_category, num_detail, dropout=0.1):
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


# ── 학습/평가 ──────────────────────────────────────────────

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


# ── 단일 버전 resume 학습 ──────────────────────────────────

def run_resume(plan: dict) -> dict:
    resume_from = plan["resume_from"]
    save_as     = plan["save_as"]
    add_epochs  = plan["add_epochs"]
    resume_lr   = plan["resume_lr"]

    train_pkl        = f"data/processed/train_{resume_from}.pkl"
    val_pkl          = f"data/processed/val_{resume_from}.pkl"
    model_output_dir = f"models/{save_as}"
    checkpoint_dir   = f"models/{save_as}_checkpoints"
    resume_model_pt  = f"models/{resume_from}/best_model.pt"
    resume_tok_dir   = f"models/{resume_from}"

    print(f"\n{'='*60}")
    print(f"  {resume_from} → {save_as}  (+{add_epochs} epochs, lr={resume_lr})")
    print(f"{'='*60}")

    # 사전 확인
    for path, name in [
        (resume_model_pt, "기존 모델"),
        (train_pkl,       "train pkl"),
        (val_pkl,         "val pkl"),
    ]:
        if not os.path.exists(path):
            print(f"  ❌ {name} 없음: {path} — 스킵")
            return {}

    os.makedirs(model_output_dir, exist_ok=True)
    os.makedirs(checkpoint_dir,   exist_ok=True)

    # 데이터
    with open(train_pkl, "rb") as f:
        train_samples = pickle.load(f)
    with open(val_pkl, "rb") as f:
        val_samples = pickle.load(f)
    print(f"  [data] train: {len(train_samples)}, val: {len(val_samples)}")

    tokenizer     = AutoTokenizer.from_pretrained(resume_tok_dir)
    train_dataset = EmotionDataset(train_samples, tokenizer, MAX_LENGTH)
    val_dataset   = EmotionDataset(val_samples,   tokenizer, MAX_LENGTH)
    train_loader  = DataLoader(train_dataset, batch_size=TRAIN_BATCH_SIZE,
                               shuffle=True,  num_workers=4, pin_memory=True)
    val_loader    = DataLoader(val_dataset,   batch_size=EVAL_BATCH_SIZE,
                               shuffle=False, num_workers=4, pin_memory=True)

    # 기존 가중치 로드
    model = MultiTaskEmotionModel(
        BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
    )
    model.load_state_dict(torch.load(resume_model_pt, map_location=DEVICE))
    model.to(DEVICE)

    # 옵티마이저
    optimizer = torch.optim.AdamW([
        {"params": model.encoder.parameters(),       "lr": resume_lr},
        {"params": model.category_head.parameters(), "lr": resume_lr * 5},
        {"params": model.detail_head.parameters(),   "lr": resume_lr * 5},
    ], weight_decay=WEIGHT_DECAY)

    total_steps  = len(train_loader) * add_epochs
    warmup_steps = int(total_steps * 0.05)
    scheduler    = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)
    criterion    = nn.CrossEntropyLoss()

    best_cat_f1 = 0.0
    history     = []

    for epoch in range(1, add_epochs + 1):
        print(f"\n  Epoch {epoch}/{add_epochs}")

        tr_loss, tr_cat_f1, tr_det_f1 = train_epoch(
            model, train_loader, optimizer, scheduler, criterion)
        val_loss, val_cat_f1, val_det_f1 = eval_epoch(
            model, val_loader, criterion)

        print(f"  train → loss: {tr_loss:.4f} | cat_f1: {tr_cat_f1:.4f} | det_f1: {tr_det_f1:.4f}")
        print(f"  val   → loss: {val_loss:.4f} | cat_f1: {val_cat_f1:.4f} | det_f1: {val_det_f1:.4f}")

        history.append({
            "epoch":       epoch,
            "resume_from": resume_from,
            "save_as":     save_as,
            "tr_loss":     round(tr_loss,    4),
            "tr_cat_f1":   round(tr_cat_f1,  4),
            "tr_det_f1":   round(tr_det_f1,  4),
            "val_loss":    round(val_loss,   4),
            "val_cat_f1":  round(val_cat_f1, 4),
            "val_det_f1":  round(val_det_f1, 4),
        })

        # 체크포인트
        ckpt_path = os.path.join(checkpoint_dir, f"epoch{epoch}")
        os.makedirs(ckpt_path, exist_ok=True)
        torch.save(model.state_dict(), os.path.join(ckpt_path, "model.pt"))
        tokenizer.save_pretrained(ckpt_path)

        # best 저장
        if val_cat_f1 > best_cat_f1:
            best_cat_f1 = val_cat_f1
            torch.save(model.state_dict(),
                       os.path.join(model_output_dir, "best_model.pt"))
            tokenizer.save_pretrained(model_output_dir)
            model.encoder.config.save_pretrained(model_output_dir)
            print(f"  ★ best model 저장 (cat_f1: {best_cat_f1:.4f})")

    with open(os.path.join(model_output_dir, "train_history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n  [완료] {save_as}  best val cat_f1: {best_cat_f1:.4f}")
    print(f"  [완료] 기존 {resume_from} 모델 그대로 유지됨 ✅")

    return {"version": save_as, "best_cat_f1": best_cat_f1}


# ── 메인 ───────────────────────────────────────────────────

def main():
    all_results = []

    for plan in RESUME_PLAN:
        result = run_resume(plan)
        if result:
            all_results.append(result)
        # GPU 메모리 정리
        torch.cuda.empty_cache()

    # 전체 결과 요약
    print("\n" + "="*60)
    print("[ 전체 resume 학습 완료 ]")
    print("="*60)
    for r in all_results:
        print(f"  {r['version']:20} best val cat_f1: {r['best_cat_f1']:.4f}")
    print("\n이제 evaluate_ai_gen.py 실행해서 6버전 비교하세요.")
    print("  python data\\finetune\\evaluate_ai_gen.py")


if __name__ == "__main__":
    main()

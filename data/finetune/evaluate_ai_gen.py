# ============================================================
# evaluate_ai_gen.py — AI 생성 데이터 + val 데이터 3버전 비교 평가
# 실행: python data\finetune\evaluate_ai_gen.py
# ============================================================

import os
import pickle
import torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel
from sklearn.metrics import f1_score, classification_report
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

import sys
sys.path.append(os.path.dirname(__file__))

from config import (
    BASE_MODEL_NAME, MAX_LENGTH, EVAL_BATCH_SIZE,
    CATEGORY_LABELS, DETAIL_LABELS,
    CATEGORY2ID, DETAIL2ID,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    ID2CATEGORY, ID2DETAIL,
)

DEVICE    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
TEST_FILE = "data/raw/ai_gen_test.txt"
VERSIONS  = ["weighted", "low_weight", "unweighted", "weighted_v2", "low_weight_v2", "unweighted_v2"]


# ── 모델 클래스 ────────────────────────────────────────────

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


# ── Dataset ────────────────────────────────────────────────

class EvalDataset(Dataset):
    def __init__(self, samples, tokenizer, max_length):
        self.samples   = samples
        self.tokenizer = tokenizer
        self.max_len   = max_length

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        s   = self.samples[idx]
        enc = self.tokenizer(
            s["text"], truncation=True,
            padding="max_length", max_length=self.max_len,
            return_tensors="pt"
        )
        return {
            "input_ids":      enc["input_ids"].squeeze(0),
            "attention_mask": enc["attention_mask"].squeeze(0),
            "token_type_ids": enc.get("token_type_ids",
                              torch.zeros(self.max_len, dtype=torch.long)).squeeze(0),
            "category_label": torch.tensor(s["category_label"], dtype=torch.long),
            "detail_label":   torch.tensor(s["detail_label"],   dtype=torch.long),
        }


# ── 데이터 로딩 ────────────────────────────────────────────

def get_group(situation):
    return "연애/결혼/출산" if any(
        k in situation for k in ["연애", "결혼", "출산"]
    ) else "기타"


def load_ai_gen(path: str):
    samples, skip = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cols = line.split(",")
            if len(cols) < 5:
                skip += 1
                continue
            gender    = cols[0].strip()
            situation = cols[1].strip()
            cat_label = cols[2].strip()
            det_label = cols[3].strip()
            sentences = [c.strip() for c in cols[4:] if c.strip()]

            if cat_label not in CATEGORY2ID or det_label not in DETAIL2ID:
                skip += 1
                continue

            for sent in sentences:
                samples.append({
                    "text":           f"[성별] {gender} [상황] {situation} [발화] {sent}",
                    "category_label": CATEGORY2ID[cat_label],
                    "detail_label":   DETAIL2ID[det_label],
                    "group":          get_group(situation),
                })

    print(f"[AI생성] 총 {len(samples)}샘플 (스킵: {skip})")
    return samples


def load_val_pkl(version: str):
    # v2는 원본 버전 pkl 사용
    base = version.replace("_v2", "")
    path = f"data/processed/val_{base}.pkl"

    # 없으면 weighted pkl로 대체 (val은 동일)
    if not os.path.exists(path):
        path = "data/processed/val_weighted.pkl"
        if not os.path.exists(path):
            path = "data/processed/val.pkl"

    with open(path, "rb") as f:
        samples = pickle.load(f)

    print(f"[val pkl] {path} → {len(samples)}샘플")
    return samples


# ── 모델 로딩 ──────────────────────────────────────────────

def load_model(version: str):
    model_dir = f"models/{version}"
    pt_path   = os.path.join(model_dir, "best_model.pt")
    if not os.path.exists(pt_path):
        print(f"  ⚠️  {pt_path} 없음 — 스킵")
        return None, None

    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model     = MultiTaskEmotionModel(
        BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
    )
    model.load_state_dict(torch.load(pt_path, map_location=DEVICE))
    model.to(DEVICE)
    model.eval()
    return model, tokenizer


# ── 평가 ───────────────────────────────────────────────────

def evaluate(model, samples, tokenizer, label=""):
    if not samples:
        return 0.0, 0.0, [], []
    dataset = EvalDataset(samples, tokenizer, MAX_LENGTH)
    loader  = DataLoader(dataset, batch_size=EVAL_BATCH_SIZE, shuffle=False)

    cat_p, cat_l, det_p, det_l = [], [], [], []
    with torch.no_grad():
        for batch in tqdm(loader, desc=f"  {label:20}", leave=False):
            ids  = batch["input_ids"].to(DEVICE)
            mask = batch["attention_mask"].to(DEVICE)
            tt   = batch["token_type_ids"].to(DEVICE)
            clo, dlo = model(ids, mask, tt)
            cat_p.extend(torch.argmax(clo, -1).cpu().numpy())
            cat_l.extend(batch["category_label"].numpy())
            det_p.extend(torch.argmax(dlo, -1).cpu().numpy())
            det_l.extend(batch["detail_label"].numpy())

    cat_f1 = f1_score(cat_l, cat_p, average="macro", zero_division=0)
    det_f1 = f1_score(det_l, det_p, average="macro", zero_division=0)
    return cat_f1, det_f1, cat_p, cat_l


# ── 메인 ───────────────────────────────────────────────────

def main():
    print(f"[device] {DEVICE}\n")

    # AI 생성 데이터 로딩
    ai_all    = load_ai_gen(TEST_FILE)
    ai_love   = [s for s in ai_all  if s["group"] == "연애/결혼/출산"]
    ai_other  = [s for s in ai_all  if s["group"] == "기타"]
    print(f"  연애/결혼/출산: {len(ai_love)}샘플")
    print(f"  기타:           {len(ai_other)}샘플\n")

    results = {}

    for version in VERSIONS:
        print(f"\n{'='*60}")
        print(f"  버전: {version}")
        print(f"{'='*60}")

        model, tokenizer = load_model(version)
        if model is None:
            continue

        # val pkl 로딩 및 분리
        val_all   = load_val_pkl(version)
        val_love  = [s for s in val_all if s.get("group") == "연애/결혼/출산"]
        val_other = [s for s in val_all if s.get("group") != "연애/결혼/출산"]
        print(f"  val 연애/결혼/출산: {len(val_love)}샘플")
        print(f"  val 기타:           {len(val_other)}샘플")

        # ── AI 생성 데이터 평가
        print("\n  [AI 생성 데이터]")
        ai_cat_all,   ai_det_all,   ai_cp, ai_cl = evaluate(model, ai_all,   tokenizer, "전체")
        ai_cat_love,  ai_det_love,  _, _          = evaluate(model, ai_love,  tokenizer, "연애/결혼/출산")
        ai_cat_other, ai_det_other, _, _          = evaluate(model, ai_other, tokenizer, "기타")

        print(f"    전체           cat_f1: {ai_cat_all:.4f}  det_f1: {ai_det_all:.4f}")
        print(f"    연애/결혼/출산 cat_f1: {ai_cat_love:.4f}  det_f1: {ai_det_love:.4f}")
        print(f"    기타           cat_f1: {ai_cat_other:.4f}  det_f1: {ai_det_other:.4f}")

        # ── val pkl 평가
        print("\n  [val 데이터]")
        val_cat_all,   val_det_all,   _, _  = evaluate(model, val_all,   tokenizer, "전체")
        val_cat_love,  val_det_love,  _, _  = evaluate(model, val_love,  tokenizer, "연애/결혼/출산")
        val_cat_other, val_det_other, _, _  = evaluate(model, val_other, tokenizer, "기타")

        print(f"    전체           cat_f1: {val_cat_all:.4f}  det_f1: {val_det_all:.4f}")
        print(f"    연애/결혼/출산 cat_f1: {val_cat_love:.4f}  det_f1: {val_det_love:.4f}")
        print(f"    기타           cat_f1: {val_cat_other:.4f}  det_f1: {val_det_other:.4f}")

        # 대분류 상세 리포트 (AI 생성 전체 기준)
        print(f"\n  [대분류 상세 — AI생성 전체]")
        print(classification_report(
            ai_cl, ai_cp,
            target_names=CATEGORY_LABELS,
            zero_division=0
        ))

        results[version] = {
            "ai_all_cat":    round(ai_cat_all,    4),
            "ai_love_cat":   round(ai_cat_love,   4),
            "ai_other_cat":  round(ai_cat_other,  4),
            "ai_all_det":    round(ai_det_all,    4),
            "ai_love_det":   round(ai_det_love,   4),
            "val_all_cat":   round(val_cat_all,   4),
            "val_love_cat":  round(val_cat_love,  4),
            "val_other_cat": round(val_cat_other, 4),
            "val_all_det":   round(val_det_all,   4),
            "val_love_det":  round(val_det_love,  4),
        }

    # ── 최종 비교표
    print("\n" + "="*65)
    print("[ 최종 비교표 ]")
    print("="*65)
    print(f"{'':25} {'weighted':>10} {'low_weight':>10} {'unweighted':>10}")
    print("-"*65)

    metrics = [
        ("─ AI생성 ─────────────", None),
        ("  전체    cat_f1",  "ai_all_cat"),
        ("  연애    cat_f1",  "ai_love_cat"),
        ("  기타    cat_f1",  "ai_other_cat"),
        ("  전체    det_f1",  "ai_all_det"),
        ("  연애    det_f1",  "ai_love_det"),
        ("─ val데이터 ───────────", None),
        ("  전체    cat_f1",  "val_all_cat"),
        ("  연애    cat_f1",  "val_love_cat"),
        ("  기타    cat_f1",  "val_other_cat"),
        ("  전체    det_f1",  "val_all_det"),
        ("  연애    det_f1",  "val_love_det"),
    ]

    for label, key in metrics:
        if key is None:
            print(f"\n{label}")
            continue
        vals = {v: results.get(v, {}).get(key) for v in VERSIONS}
        best = max((v for v in vals.values() if v is not None), default=None)
        row  = f"  {label:23}"
        for v in VERSIONS:
            val  = vals.get(v)
            mark = " ✅" if val == best else "   "
            row += f"  {val:>8}{mark}" if val is not None else f"  {'N/A':>8}   "
        print(row)

    print("\n★ 핵심 지표: AI생성 연애 cat_f1 / val 연애 cat_f1")


if __name__ == "__main__":
    main()

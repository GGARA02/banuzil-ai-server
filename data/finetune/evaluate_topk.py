# ============================================================
# evaluate_topk.py — 6버전 모델 Top1/Top2 정확도 비교 평가
# 실행: python data\finetune\evaluate_topk.py
# ============================================================

import os
import pickle
import torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel
from sklearn.metrics import f1_score
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm

import sys
sys.path.append(os.path.dirname(__file__))

from config import (
    BASE_MODEL_NAME, MAX_LENGTH, EVAL_BATCH_SIZE,
    CATEGORY_LABELS, DETAIL_LABELS,
    CATEGORY2ID, DETAIL2ID,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
)

DEVICE    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
TEST_FILE = "data/raw/ai_gen_test2.txt"   # ← 변경 가능
VERSIONS  = [
    "weighted", "low_weight", "unweighted",
    "weighted_v2", "low_weight_v2", "unweighted_v2"
]


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


def load_ai_gen(path):
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
    print(f"[AI생성] {len(samples)}샘플 (스킵: {skip})")
    return samples


def load_val_pkl(version):
    base = version.replace("_v2", "")
    for path in [
        f"data/processed/val_{base}.pkl",
        "data/processed/val_weighted.pkl",
        "data/processed/val.pkl",
    ]:
        if os.path.exists(path):
            with open(path, "rb") as f:
                samples = pickle.load(f)
            print(f"[val pkl] {path} → {len(samples)}샘플")
            return samples
    return []


def load_model(version):
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


# ── Top-K 평가 ─────────────────────────────────────────────

def evaluate_topk(model, samples, tokenizer, label="", k=2):
    if not samples:
        return 0.0, 0.0, 0.0, 0.0, [], []

    dataset = EvalDataset(samples, tokenizer, MAX_LENGTH)
    loader  = DataLoader(dataset, batch_size=EVAL_BATCH_SIZE, shuffle=False)

    cat_top1_p, cat_top2_hit = [], []
    det_top1_p, det_top2_hit = [], []
    cat_labels, det_labels   = [], []

    with torch.no_grad():
        for batch in tqdm(loader, desc=f"  {label:22}", leave=False):
            ids  = batch["input_ids"].to(DEVICE)
            mask = batch["attention_mask"].to(DEVICE)
            tt   = batch["token_type_ids"].to(DEVICE)
            cl   = batch["category_label"]
            dl   = batch["detail_label"]

            clo, dlo = model(ids, mask, tt)

            # 대분류
            cat_topk = torch.topk(clo, k, dim=-1).indices.cpu()
            cat_top1_p.extend(cat_topk[:, 0].numpy())
            cat_top2_hit.extend(
                [int(cl[i].item() in cat_topk[i].tolist()) for i in range(len(cl))]
            )
            cat_labels.extend(cl.numpy())

            # 소분류
            det_topk = torch.topk(dlo, k, dim=-1).indices.cpu()
            det_top1_p.extend(det_topk[:, 0].numpy())
            det_top2_hit.extend(
                [int(dl[i].item() in det_topk[i].tolist()) for i in range(len(dl))]
            )
            det_labels.extend(dl.numpy())

    cat_top1_f1  = f1_score(cat_labels, cat_top1_p, average="macro", zero_division=0)
    cat_top2_acc = sum(cat_top2_hit) / len(cat_top2_hit)
    det_top1_f1  = f1_score(det_labels, det_top1_p, average="macro", zero_division=0)
    det_top2_acc = sum(det_top2_hit) / len(det_top2_hit)

    return cat_top1_f1, cat_top2_acc, det_top1_f1, det_top2_acc, cat_top1_p, cat_labels


# ── 비교표 출력 ────────────────────────────────────────────

def print_table(title, results, metrics):
    print(f"\n{'='*80}")
    print(f"[ {title} ]")
    print(f"{'='*80}")
    header = f"{'':26}"
    for v in VERSIONS:
        short = v[:13]
        header += f" {short:>13}"
    print(header)
    print("-"*80)

    for label, key in metrics:
        if key is None:
            print(f"\n{label}")
            continue
        vals = {v: results.get(v, {}).get(key) for v in VERSIONS}
        best = max((x for x in vals.values() if x is not None), default=None)
        row  = f"  {label:24}"
        for v in VERSIONS:
            val  = vals.get(v)
            mark = "✅" if val == best else "  "
            row += f"  {val:.4f}{mark}" if val is not None else f"  {'N/A':>6}  "
        print(row)


# ── 메인 ───────────────────────────────────────────────────

def main():
    print(f"[device] {DEVICE}\n")

    # AI 생성 데이터
    ai_all   = load_ai_gen(TEST_FILE)
    ai_love  = [s for s in ai_all if s["group"] == "연애/결혼/출산"]
    ai_other = [s for s in ai_all if s["group"] == "기타"]
    print(f"  AI생성 연애: {len(ai_love)}  기타: {len(ai_other)}\n")

    results = {}

    for version in VERSIONS:
        print(f"\n{'='*55}")
        print(f"  버전: {version}")
        print(f"{'='*55}")

        model, tokenizer = load_model(version)
        if model is None:
            continue

        # val pkl 로딩 및 분리
        val_all   = load_val_pkl(version)
        val_love  = [s for s in val_all if s.get("group") == "연애/결혼/출산"]
        val_other = [s for s in val_all if s.get("group") != "연애/결혼/출산"]
        print(f"  val 연애: {len(val_love)}  기타: {len(val_other)}")

        # AI생성 평가
        a_c1, a_c2, a_d1, a_d2, _, _ = evaluate_topk(model, ai_all,   tokenizer, "AI전체")
        l_c1, l_c2, l_d1, l_d2, _, _ = evaluate_topk(model, ai_love,  tokenizer, "AI연애")
        o_c1, o_c2, o_d1, o_d2, _, _ = evaluate_topk(model, ai_other, tokenizer, "AI기타")

        # val 평가
        va_c1, va_c2, va_d1, va_d2, _, _ = evaluate_topk(model, val_all,   tokenizer, "val전체")
        vl_c1, vl_c2, vl_d1, vl_d2, _, _ = evaluate_topk(model, val_love,  tokenizer, "val연애")
        vo_c1, vo_c2, vo_d1, vo_d2, _, _ = evaluate_topk(model, val_other, tokenizer, "val기타")

        print(f"\n  AI생성  전체  cat Top1:{a_c1:.4f}  Top2:{a_c2:.4f}  det Top1:{a_d1:.4f}  Top2:{a_d2:.4f}")
        print(f"          연애  cat Top1:{l_c1:.4f}  Top2:{l_c2:.4f}  det Top1:{l_d1:.4f}  Top2:{l_d2:.4f}")
        print(f"          기타  cat Top1:{o_c1:.4f}  Top2:{o_c2:.4f}  det Top1:{o_d1:.4f}  Top2:{o_d2:.4f}")
        print(f"\n  val     전체  cat Top1:{va_c1:.4f}  Top2:{va_c2:.4f}  det Top1:{va_d1:.4f}  Top2:{va_d2:.4f}")
        print(f"          연애  cat Top1:{vl_c1:.4f}  Top2:{vl_c2:.4f}  det Top1:{vl_d1:.4f}  Top2:{vl_d2:.4f}")
        print(f"          기타  cat Top1:{vo_c1:.4f}  Top2:{vo_c2:.4f}  det Top1:{vo_d1:.4f}  Top2:{vo_d2:.4f}")

        results[version] = {
            # AI생성
            "ai_all_c1":   round(a_c1,  4), "ai_all_c2":   round(a_c2,  4),
            "ai_all_d1":   round(a_d1,  4), "ai_all_d2":   round(a_d2,  4),
            "ai_love_c1":  round(l_c1,  4), "ai_love_c2":  round(l_c2,  4),
            "ai_love_d1":  round(l_d1,  4), "ai_love_d2":  round(l_d2,  4),
            "ai_other_c1": round(o_c1,  4), "ai_other_c2": round(o_c2,  4),
            "ai_other_d1": round(o_d1,  4), "ai_other_d2": round(o_d2,  4),
            # val
            "val_all_c1":  round(va_c1, 4), "val_all_c2":  round(va_c2, 4),
            "val_all_d1":  round(va_d1, 4), "val_all_d2":  round(va_d2, 4),
            "val_love_c1": round(vl_c1, 4), "val_love_c2": round(vl_c2, 4),
            "val_love_d1": round(vl_d1, 4), "val_love_d2": round(vl_d2, 4),
            "val_other_c1":round(vo_c1, 4), "val_other_c2":round(vo_c2, 4),
            "val_other_d1":round(vo_d1, 4), "val_other_d2":round(vo_d2, 4),
        }

    # ── 비교표 출력
    ai_metrics = [
        ("─ AI생성 ─────────────",  None),
        ("  전체  cat Top1 F1",     "ai_all_c1"),
        ("        cat Top2 Acc",    "ai_all_c2"),
        ("        det Top1 F1",     "ai_all_d1"),
        ("        det Top2 Acc",    "ai_all_d2"),
        ("  연애  cat Top1 F1",     "ai_love_c1"),
        ("        cat Top2 Acc",    "ai_love_c2"),
        ("        det Top1 F1",     "ai_love_d1"),
        ("        det Top2 Acc",    "ai_love_d2"),
        ("  기타  cat Top1 F1",     "ai_other_c1"),
        ("        cat Top2 Acc",    "ai_other_c2"),
        ("        det Top1 F1",     "ai_other_d1"),
        ("        det Top2 Acc",    "ai_other_d2"),
    ]

    val_metrics = [
        ("─ val 데이터 ─────────",  None),
        ("  전체  cat Top1 F1",     "val_all_c1"),
        ("        cat Top2 Acc",    "val_all_c2"),
        ("        det Top1 F1",     "val_all_d1"),
        ("        det Top2 Acc",    "val_all_d2"),
        ("  연애  cat Top1 F1",     "val_love_c1"),
        ("        cat Top2 Acc",    "val_love_c2"),
        ("        det Top1 F1",     "val_love_d1"),
        ("        det Top2 Acc",    "val_love_d2"),
        ("  기타  cat Top1 F1",     "val_other_c1"),
        ("        cat Top2 Acc",    "val_other_c2"),
        ("        det Top1 F1",     "val_other_d1"),
        ("        det Top2 Acc",    "val_other_d2"),
    ]

    print_table("최종 비교표 — AI생성 데이터", results, ai_metrics)
    print_table("최종 비교표 — val 데이터",    results, val_metrics)
    print("\n★ 핵심 지표: 연애 cat Top2 Acc / 연애 det Top2 Acc")


if __name__ == "__main__":
    main()

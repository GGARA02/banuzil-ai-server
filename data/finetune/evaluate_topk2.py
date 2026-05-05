# ============================================================
# evaluate_topk.py — 6버전 모델 소분류 Top1/Top2/Top3 비교
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
    CATEGORY2ID, DETAIL2ID,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
)

DEVICE    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
TEST_FILE = "data/raw/ai_gen_test2.txt"
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
            "detail_label":   torch.tensor(s["detail_label"], dtype=torch.long),
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
                    "text":         f"[성별] {gender} [상황] {situation} [발화] {sent}",
                    "detail_label": DETAIL2ID[det_label],
                    "group":        get_group(situation),
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


# ── 소분류 Top1/2/3 평가 ───────────────────────────────────

def evaluate_det_topk(model, samples, tokenizer, label=""):
    if not samples:
        return {"d1": 0.0, "d2": 0.0, "d3": 0.0}

    dataset = EvalDataset(samples, tokenizer, MAX_LENGTH)
    loader  = DataLoader(dataset, batch_size=EVAL_BATCH_SIZE, shuffle=False)

    det_top1_p   = []
    det_top2_hit = []
    det_top3_hit = []
    det_labels   = []

    with torch.no_grad():
        for batch in tqdm(loader, desc=f"  {label:22}", leave=False):
            ids  = batch["input_ids"].to(DEVICE)
            mask = batch["attention_mask"].to(DEVICE)
            tt   = batch["token_type_ids"].to(DEVICE)
            dl   = batch["detail_label"]

            _, dlo = model(ids, mask, tt)

            det_topk = torch.topk(dlo, 3, dim=-1).indices.cpu()
            det_top1_p.extend(det_topk[:, 0].numpy())
            det_top2_hit.extend(
                [int(dl[i].item() in det_topk[i, :2].tolist()) for i in range(len(dl))]
            )
            det_top3_hit.extend(
                [int(dl[i].item() in det_topk[i, :3].tolist()) for i in range(len(dl))]
            )
            det_labels.extend(dl.numpy())

    return {
        "d1": round(f1_score(det_labels, det_top1_p, average="macro", zero_division=0), 4),
        "d2": round(sum(det_top2_hit) / len(det_top2_hit), 4),
        "d3": round(sum(det_top3_hit) / len(det_top3_hit), 4),
    }


# ── 비교표 출력 ────────────────────────────────────────────

def print_table(title, results, metrics):
    print(f"\n{'='*85}")
    print(f"[ {title} ]")
    print(f"{'='*85}")
    header = f"{'':26}"
    for v in VERSIONS:
        header += f" {v[:13]:>13}"
    print(header)
    print("-"*85)

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
    print(f"[device] {DEVICE}")
    print(f"[설정] 소분류(det): Top1 F1 / Top2 Acc / Top3 Acc\n")

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

        val_all   = load_val_pkl(version)
        val_love  = [s for s in val_all if s.get("group") == "연애/결혼/출산"]
        val_other = [s for s in val_all if s.get("group") != "연애/결혼/출산"]
        print(f"  val 연애: {len(val_love)}  기타: {len(val_other)}")

        r = {}
        for tag, samples in [
            ("ai_all",   ai_all),
            ("ai_love",  ai_love),
            ("ai_other", ai_other),
            ("val_all",  val_all),
            ("val_love", val_love),
            ("val_other",val_other),
        ]:
            m = evaluate_det_topk(model, samples, tokenizer, tag)
            for k, v in m.items():
                r[f"{tag}_{k}"] = v

        print(f"\n  AI생성 연애  det Top1:{r['ai_love_d1']:.4f}  Top2:{r['ai_love_d2']:.4f}  Top3:{r['ai_love_d3']:.4f}")
        print(f"  val    연애  det Top1:{r['val_love_d1']:.4f}  Top2:{r['val_love_d2']:.4f}  Top3:{r['val_love_d3']:.4f}")
        results[version] = r

    # ── AI생성 비교표
    ai_metrics = [
        ("─ AI생성 ─────────────",   None),
        ("  전체  det Top1 F1",       "ai_all_d1"),
        ("        det Top2 Acc",      "ai_all_d2"),
        ("        det Top3 Acc",      "ai_all_d3"),
        ("  연애  det Top1 F1",       "ai_love_d1"),
        ("        det Top2 Acc",      "ai_love_d2"),
        ("        det Top3 Acc ★",    "ai_love_d3"),
        ("  기타  det Top1 F1",       "ai_other_d1"),
        ("        det Top2 Acc",      "ai_other_d2"),
        ("        det Top3 Acc",      "ai_other_d3"),
    ]

    val_metrics = [
        ("─ val 데이터 ─────────",   None),
        ("  전체  det Top1 F1",       "val_all_d1"),
        ("        det Top2 Acc",      "val_all_d2"),
        ("        det Top3 Acc",      "val_all_d3"),
        ("  연애  det Top1 F1",       "val_love_d1"),
        ("        det Top2 Acc",      "val_love_d2"),
        ("        det Top3 Acc ★",    "val_love_d3"),
        ("  기타  det Top1 F1",       "val_other_d1"),
        ("        det Top2 Acc",      "val_other_d2"),
        ("        det Top3 Acc",      "val_other_d3"),
    ]

    print_table("소분류 Top-K 비교 — AI생성 데이터", results, ai_metrics)
    print_table("소분류 Top-K 비교 — val 데이터",    results, val_metrics)
    print("\n★ 핵심: 연애 det Top3 Acc — 상담AI에게 소분류 3개 제공 시 정답 포함 비율")


if __name__ == "__main__":
    main()

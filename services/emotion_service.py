# ============================================================
# emotion_service.py — 감정 분석 서비스 (HierarchicalEmotionModel)
#
# 대분류 + 소분류: concat_unweight 모델 단일 추론
#   대분류 예측 → category embedding → 소분류 마스킹 추론
# ============================================================

import os
import json
import torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel
from typing import List

import importlib.util


def _load_finetune_config():
    """data/finetune/config.py를 루트 config/ 패키지와 충돌 없이 직접 로드"""
    _config_path = os.path.join(os.path.dirname(__file__), "../data/finetune/config.py")
    _spec = importlib.util.spec_from_file_location("finetune_config", _config_path)
    _mod  = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


_fc = _load_finetune_config()
BASE_MODEL_NAME      = _fc.BASE_MODEL_NAME
MAX_LENGTH           = _fc.MAX_LENGTH
NUM_CATEGORY_LABELS  = _fc.NUM_CATEGORY_LABELS
NUM_DETAIL_LABELS    = _fc.NUM_DETAIL_LABELS
ID2CATEGORY          = _fc.ID2CATEGORY
ID2DETAIL            = _fc.ID2DETAIL

MODEL_DIR  = "models/concat_unweight"
CAT_TOPK   = 2
DET_TOPK   = 3

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _load_cat_to_detail_ids(model_dir: str) -> dict:
    """model_meta.json에서 cat_to_detail_ids 매핑 로드"""
    meta_path = os.path.join(model_dir, "model_meta.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return {int(k): v for k, v in meta["cat_to_detail_ids"].items()}


CAT_TO_DETAIL_IDS = _load_cat_to_detail_ids(MODEL_DIR)


def _build_detail_mask(category_ids, num_details):
    """대분류 예측 결과에 따라 소분류 logits 마스킹"""
    batch_size = len(category_ids)
    mask = torch.full((batch_size, num_details), float("-inf"))
    for i, cat_id in enumerate(category_ids):
        cid = cat_id if isinstance(cat_id, int) else cat_id.item()
        for d in CAT_TO_DETAIL_IDS[cid]:
            mask[i, d] = 0.0
    return mask


class HierarchicalEmotionModel(nn.Module):
    """대분류 → category embedding → 소분류 마스킹 모델"""

    def __init__(self, model_name, num_category, num_detail, dropout=0.1):
        super().__init__()
        self.encoder = AutoModel.from_pretrained(model_name)
        hidden_size  = self.encoder.config.hidden_size
        self.num_detail = num_detail
        self.dropout = nn.Dropout(dropout)

        self.category_head = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_category),
        )
        self.category_embedding = nn.Embedding(num_category, hidden_size // 4)

        detail_input_size = hidden_size + hidden_size // 4
        self.detail_head = nn.Sequential(
            nn.Linear(detail_input_size, hidden_size // 2),
            nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden_size // 2, num_detail),
        )

    def forward(self, input_ids, attention_mask, token_type_ids=None,
                category_ids=None):
        out = self.encoder(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
        )
        cls = self.dropout(out.last_hidden_state[:, 0, :])

        cat_logits = self.category_head(cls)
        if category_ids is None:
            category_ids = torch.argmax(cat_logits, dim=-1)

        cat_emb = self.category_embedding(category_ids)
        detail_input = torch.cat([cls, cat_emb], dim=-1)
        detail_logits = self.detail_head(detail_input)

        mask = _build_detail_mask(category_ids, self.num_detail).to(detail_logits.device)
        detail_logits = detail_logits + mask

        return cat_logits, detail_logits


class EmotionService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        print(f"[EmotionService] 모델 로딩 중... (device: {DEVICE})")
        self._load_model()
        self._initialized = True
        print("[EmotionService] 로딩 완료")

    def _load_model(self):
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
        self.model = HierarchicalEmotionModel(
            BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
        )
        self.model.load_state_dict(
            torch.load(
                os.path.join(MODEL_DIR, "best_model.pt"),
                map_location=DEVICE,
            )
        )
        self.model.to(DEVICE)
        self.model.eval()

    def _encode(self, text: str) -> dict:
        enc = self.tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=MAX_LENGTH,
            return_tensors="pt",
        )
        input_ids      = enc["input_ids"].to(DEVICE)
        attention_mask = enc["attention_mask"].to(DEVICE)
        token_type_ids = enc.get(
            "token_type_ids",
            torch.zeros(MAX_LENGTH, dtype=torch.long)
        )
        if token_type_ids.dim() == 1:
            token_type_ids = token_type_ids.unsqueeze(0)
        token_type_ids = token_type_ids.to(DEVICE)
        return {
            "input_ids":      input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }

    def analyze(
        self,
        text:      str,
        gender:    str = "미상",
        situation: str = "연애",
    ) -> dict:
        input_text = f"[성별] {gender} [상황] {situation} [발화] {text}"

        with torch.no_grad():
            enc = self._encode(input_text)
            cat_logits, det_logits = self.model(**enc, category_ids=None)

            cat_probs = torch.softmax(cat_logits, dim=-1)[0]
            det_probs = torch.softmax(det_logits, dim=-1)[0]
            cat_topk  = torch.topk(cat_probs, CAT_TOPK)
            det_topk  = torch.topk(det_probs, DET_TOPK)

        category = [
            {
                "rank":  i + 1,
                "label": ID2CATEGORY[idx.item()],
                "score": round(score.item(), 4),
            }
            for i, (score, idx) in enumerate(zip(cat_topk.values, cat_topk.indices))
        ]
        detail = [
            {
                "rank":  i + 1,
                "label": ID2DETAIL[idx.item()],
                "score": round(score.item(), 4),
            }
            for i, (score, idx) in enumerate(zip(det_topk.values, det_topk.indices))
        ]

        return {
            "input": {
                "gender":    gender,
                "situation": situation,
                "text":      text,
            },
            "model": os.path.basename(MODEL_DIR),
            "category": category,
            "detail":   detail,
        }

    def analyze_batch(self, items: List[dict]) -> List[dict]:
        return [
            self.analyze(
                text      = item.get("text", ""),
                gender    = item.get("gender", "미상"),
                situation = item.get("situation", "연애"),
            )
            for item in items
        ]

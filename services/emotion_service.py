# ============================================================
# emotion_service.py — 앙상블 감정 분석 서비스
#
# 대분류: unweighted 모델 → Top2
# 소분류: low_weight 모델 → Top3
# ============================================================

import os
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

CAT_MODEL_DIR = "models/unweighted"
DET_MODEL_DIR = "models/low_weight"
CAT_TOPK      = 2
DET_TOPK      = 3

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


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
        self._load_models()
        self._initialized = True
        print("[EmotionService] 로딩 완료 ✅")

    def _load_model(self, model_dir: str):
        tokenizer = AutoTokenizer.from_pretrained(model_dir)
        model     = MultiTaskEmotionModel(
            BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
        )
        model.load_state_dict(
            torch.load(
                os.path.join(model_dir, "best_model.pt"),
                map_location=DEVICE,
            )
        )
        model.to(DEVICE)
        model.eval()
        return model, tokenizer

    def _load_models(self):
        self.cat_model, self.cat_tokenizer = self._load_model(CAT_MODEL_DIR)
        self.det_model, self.det_tokenizer = self._load_model(DET_MODEL_DIR)

    def _encode(self, tokenizer, text: str) -> dict:
        enc = tokenizer(
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
            cat_enc       = self._encode(self.cat_tokenizer, input_text)
            cat_logits, _ = self.cat_model(**cat_enc)
            cat_probs     = torch.softmax(cat_logits, dim=-1)[0]
            cat_topk      = torch.topk(cat_probs, CAT_TOPK)

            det_enc       = self._encode(self.det_tokenizer, input_text)
            _, det_logits = self.det_model(**det_enc)
            det_probs     = torch.softmax(det_logits, dim=-1)[0]
            det_topk      = torch.topk(det_probs, DET_TOPK)

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
            "models": {
                "category_model": os.path.basename(CAT_MODEL_DIR),
                "detail_model":   os.path.basename(DET_MODEL_DIR),
            },
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

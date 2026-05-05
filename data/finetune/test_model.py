# ============================================================
# test_model.py — 직접 입력해서 감정 분류 테스트
# 실행: python data\finetune\test_model.py
# ============================================================

import torch
import torch.nn as nn
from transformers import AutoTokenizer, AutoModel
from config import (
    BASE_MODEL_NAME, MAX_LENGTH,
    NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS,
    ID2CATEGORY, ID2DETAIL,
    MODEL_OUTPUT_DIR,
)

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


# ── 모델 로딩 ──────────────────────────────────────────────
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[device] {DEVICE}")
print("모델 로딩 중...")

tokenizer = AutoTokenizer.from_pretrained(MODEL_OUTPUT_DIR)
model     = MultiTaskEmotionModel(
    BASE_MODEL_NAME, NUM_CATEGORY_LABELS, NUM_DETAIL_LABELS
)
model.load_state_dict(
    torch.load(MODEL_OUTPUT_DIR + "/best_model.pt", map_location=DEVICE)
)
model.to(DEVICE)
model.eval()
print("모델 로딩 완료\n")


# ── 추론 함수 ──────────────────────────────────────────────
def predict(gender: str, situation: str, text: str) -> dict:
    input_text = f"[성별] {gender} [상황] {situation} [발화] {text}"
    enc = tokenizer(
        input_text,
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
    ).to(DEVICE)
    if token_type_ids.dim() == 1:
        token_type_ids = token_type_ids.unsqueeze(0)

    with torch.no_grad():
        cat_logits, det_logits = model(input_ids, attention_mask, token_type_ids)

    # 대분류 상위 3개
    cat_probs  = torch.softmax(cat_logits, dim=-1)[0]
    cat_top3   = torch.topk(cat_probs, 3)

    # 소분류 상위 3개
    det_probs  = torch.softmax(det_logits, dim=-1)[0]
    det_top3   = torch.topk(det_probs, 3)

    return {
        "input":    input_text,
        "cat_top3": [(ID2CATEGORY[i.item()], round(p.item(), 4))
                     for p, i in zip(cat_top3.values, cat_top3.indices)],
        "det_top3": [(ID2DETAIL[i.item()],   round(p.item(), 4))
                     for p, i in zip(det_top3.values, det_top3.indices)],
    }


# ── 인터랙티브 루프 ────────────────────────────────────────
print("=" * 55)
print("  감정 분류 테스트")
print("  종료: 'q' 입력")
print("=" * 55)

# 성별/상황 기본값 설정
print("\n성별 입력 (여성/남성, 기본값: 여성):", end=" ")
gender = input().strip() or "여성"

print("상황 입력 (연애,결혼 등, 기본값: 연애,결혼):", end=" ")
situation = input().strip() or "연애,결혼"

print(f"\n[설정] 성별: {gender} | 상황: {situation}")
print("발화를 입력하세요. 성별/상황 변경: 's' 입력\n")

while True:
    print("발화 > ", end="")
    text = input().strip()

    if text.lower() == "q":
        print("종료합니다.")
        break

    if text.lower() == "s":
        print("성별 입력 (여성/남성):", end=" ")
        gender = input().strip() or gender
        print("상황 입력:", end=" ")
        situation = input().strip() or situation
        print(f"[변경] 성별: {gender} | 상황: {situation}\n")
        continue

    if not text:
        continue

    result = predict(gender, situation, text)

    print()
    print("┌─────────────────────────────────────")
    print(f"│ 대분류 (감정 카테고리)")
    for i, (label, prob) in enumerate(result["cat_top3"]):
        bar = "█" * int(prob * 20)
        mark = " ←" if i == 0 else ""
        print(f"│  {i+1}. {label:6s}  {prob:.4f}  {bar}{mark}")
    print("│")
    print(f"│ 소분류 (세부 감정)")
    for i, (label, prob) in enumerate(result["det_top3"]):
        bar = "█" * int(prob * 20)
        mark = " ←" if i == 0 else ""
        print(f"│  {i+1}. {label:12s}  {prob:.4f}  {bar}{mark}")
    print("└─────────────────────────────────────\n")

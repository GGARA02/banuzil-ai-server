# ============================================================
# augment.py — 한국어 텍스트 증강 모듈
# 연애/결혼/출산 카테고리 발화에 집중 적용
# ============================================================

import random
import re
from typing import List
from config import SYNONYM_REPLACE_PROB, RANDOM_DELETE_PROB, SWAP_PROB

# ── 감정 표현 동의어 사전 ──────────────────────────────────
# 연인/부부 갈등 맥락에서 자주 등장하는 표현 중심
SYNONYM_DICT = {
    # 분노/짜증
    "화가 난다": ["열받는다", "짜증난다", "화가 치민다", "열이 받는다"],
    "화가 나": ["열받아", "짜증나", "열이 받아", "화가 치밀어"],
    "짜증나": ["열받아", "화나", "짜증스러워", "화가 나"],
    "짜증난다": ["열받는다", "화난다", "짜증스럽다"],
    "열받아": ["화나", "짜증나", "열이 올라"],
    "화났어": ["열받았어", "짜증났어", "화가 났어"],
    "화가 났다": ["열이 받았다", "짜증이 났다", "화가 치밀었다"],

    # 슬픔/외로움
    "슬퍼": ["울적해", "마음이 아파", "슬픔이 밀려와", "눈물이 나"],
    "슬프다": ["울적하다", "마음이 아프다", "슬픔이 밀려온다"],
    "외로워": ["쓸쓸해", "혼자인 것 같아", "고독해", "허전해"],
    "외롭다": ["쓸쓸하다", "고독하다", "허전하다"],
    "허전해": ["외로워", "쓸쓸해", "빈자리가 느껴져"],
    "눈물이 나": ["울고 싶어", "눈물이 흘러", "슬퍼"],

    # 불안/두려움
    "불안해": ["걱정돼", "두려워", "초조해", "마음이 불안해"],
    "불안하다": ["걱정된다", "두렵다", "초조하다"],
    "걱정돼": ["불안해", "염려돼", "마음이 쓰여", "신경 쓰여"],
    "걱정된다": ["불안하다", "염려된다", "마음이 쓰인다"],
    "두려워": ["무서워", "겁나", "불안해", "떨려"],
    "무서워": ["두려워", "겁나", "두렵다"],

    # 상처/배신
    "상처받았어": ["마음이 아팠어", "실망했어", "배신감을 느꼈어"],
    "상처받았다": ["마음이 아팠다", "실망했다", "배신감을 느꼈다"],
    "배신당한 것 같아": ["뒤통수 맞은 것 같아", "믿었는데 실망이야"],
    "실망했어": ["상처받았어", "기대가 무너졌어", "믿었는데"],
    "억울해": ["억울하다", "분하다", "억울한 마음이 들어"],

    # 관계 관련
    "연락을 안 해": ["연락이 없어", "연락을 끊었어", "연락이 안 와"],
    "연락 안 해": ["연락이 없어", "연락을 끊었어", "연락도 안 해"],
    "왜 연락을": ["왜 연락이", "어째서 연락을", "도대체 연락을"],
    "혼자인 것 같아": ["외로운 것 같아", "나 혼자인 것 같아", "버려진 것 같아"],
    "이해해줘": ["알아줘", "내 마음 알아줘", "좀 이해해"],
    "미워": ["싫어", "밉다", "너무 싫어"],
    "보고 싶어": ["그리워", "만나고 싶어", "생각나"],
    "사랑해": ["좋아해", "애정을 느껴", "소중해"],
    "헤어지자": ["그만 만나자", "우리 끝내자", "이별하자"],

    # 어미 변형 (공통)
    "것 같아": ["것 같아요", "것 같은데", "것 같다고"],
    "인 것 같아": ["인 것 같은데", "인 것 같다고"],
}

# ── 증강 함수들 ────────────────────────────────────────────

def synonym_replace(text: str, prob: float = SYNONYM_REPLACE_PROB) -> str:
    """
    동의어 치환 증강.
    텍스트 내 사전 등록 표현을 prob 확률로 동의어로 교체.
    """
    for original, synonyms in SYNONYM_DICT.items():
        if original in text and random.random() < prob:
            replacement = random.choice(synonyms)
            text = text.replace(original, replacement, 1)
    return text


def random_delete(text: str, prob: float = RANDOM_DELETE_PROB) -> str:
    """
    랜덤 어절 삭제 증강.
    어절(공백 기준) 단위로 prob 확률로 삭제.
    2어절 이하 문장은 삭제하지 않음 (너무 짧아지면 의미 손실 큼).
    """
    words = text.split()
    if len(words) <= 2:
        return text
    result = [w for w in words if random.random() > prob]
    # 결과가 비면 원본 반환
    return " ".join(result) if result else text


def random_swap(text: str, prob: float = SWAP_PROB) -> str:
    """
    어절 순서 교환 증강.
    인접한 두 어절을 prob 확률로 교환.
    문장 앞뒤 어절(첫 번째, 마지막)은 교환하지 않음.
    """
    words = text.split()
    if len(words) < 3:
        return text
    words = list(words)
    for i in range(1, len(words) - 1):
        if random.random() < prob:
            j = random.randint(1, len(words) - 2)
            words[i], words[j] = words[j], words[i]
    return " ".join(words)


def augment_text(text: str) -> List[str]:
    """
    단일 텍스트에서 증강 샘플 목록 생성.
    세 가지 기법을 조합하여 다양한 변형 생성.
    원본은 포함하지 않음 (호출부에서 원본 + 증강 합산).
    """
    augmented = set()

    # 증강 조합 시도 횟수 (다양성 확보)
    for _ in range(6):
        t = text
        # 기법을 랜덤 순서로 적용
        ops = [synonym_replace, random_delete, random_swap]
        random.shuffle(ops)
        for op in ops:
            if random.random() < 0.6:
                t = op(t)
        if t != text and len(t.strip()) > 2:
            augmented.add(t)

    return list(augmented)


def augment_dataframe_rows(rows: List[dict], multiplier: int = 2) -> List[dict]:
    """
    행 목록을 받아 증강된 행 목록 반환.
    원본 행은 포함하지 않음.

    Args:
        rows: [{"text": ..., "category_label": ..., "detail_label": ...,
                "gender": ..., "situation": ...}, ...]
        multiplier: 행당 최대 증강 샘플 수

    Returns:
        증강된 행 목록 (원본 미포함)
    """
    augmented_rows = []
    for row in rows:
        candidates = augment_text(row["text"])
        # multiplier 개수만큼 샘플링 (후보가 적으면 있는 만큼)
        selected = random.sample(candidates, min(multiplier, len(candidates)))
        for aug_text in selected:
            augmented_rows.append({
                "text":           aug_text,
                "category_label": row["category_label"],
                "detail_label":   row["detail_label"],
                "gender":         row["gender"],
                "situation":      row["situation"],
                "is_augmented":   True,
            })
    return augmented_rows


# ── 테스트 ─────────────────────────────────────────────────
if __name__ == "__main__":
    random.seed(42)
    test_sentences = [
        "왜 연락을 안 해? 화가 난다.",
        "혼자인 것 같아서 외로워.",
        "상처받았어. 믿었는데 실망했어.",
        "불안해서 잠도 못 자겠어.",
    ]
    for s in test_sentences:
        print(f"\n[원본] {s}")
        results = augment_text(s)
        for i, r in enumerate(results[:3], 1):
            print(f"  [증강{i}] {r}")

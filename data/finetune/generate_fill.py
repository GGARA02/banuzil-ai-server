# ============================================================
# generate_fill.py — 부족 소분류 집중 보충 생성기
#
# 기존 train_weighted.pkl 분포를 분석하여
# TARGET 미만인 소분류만 골라서 GPT로 보충 생성.
#
# 실행: python data/finetune/generate_fill.py
#       python data/finetune/generate_fill.py --target 6000
# 출력: data/processed/ai_fill_{count}.pkl
# ============================================================

import asyncio
import argparse
import json
import pickle
import random
import time
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from data.finetune.config import (
    CATEGORY_LABELS, CATEGORY_TO_DETAIL,
    CATEGORY2ID, DETAIL2ID, CATEGORY_KEYWORD_MAP,
    ID2CATEGORY, ID2DETAIL,
)

# ── 설정 ─────────────────────────────────────────────────────
GENDERS = ["여성", "남성"]

SITUATIONS = [
    "연애", "결혼", "출산", "대인관계",
    "직장", "학교", "가족", "건강",
    "재정", "자아/성장",
]

MODEL = "gpt-5.4-mini"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "processed"
TRAIN_PKL = PROCESSED_DIR / "train_weighted.pkl"

SYSTEM_PROMPT = """너는 한국어 감성 대화 데이터셋을 생성하는 전문가야.
주어진 조건(성별, 상황, 대분류 감정, 소분류 감정)에 맞는 자연스러운 1인칭 발화 세트를 생성해.

원본 데이터 형식:
- 한 사람이 상담사와 대화하는 상황에서, 같은 감정을 가진 사람 발화가 1~3개 존재
- 사람문장1: 처음 감정을 표현하는 발화
- 사람문장2: 상담사 응답 후 좀 더 구체적으로 이야기하는 발화
- 사람문장3: (선택) 추가로 심경을 이어가는 발화

규칙:
1. 실제 사람이 일상에서 말할 법한 자연스러운 구어체로 작성
2. 각 발화는 1~3문장, 20~80자 내외
3. 해당 감정이 명확히 드러나되 감정 단어를 직접 사용하지 않아도 됨
4. 상황 키워드에 맞는 구체적인 맥락을 포함할 것
5. 다양한 연령대, 말투를 섞어서 생성
6. 사람문장1→2→3은 하나의 대화 흐름 안에서 자연스럽게 이어져야 함"""

USER_PROMPT_TEMPLATE = """다음 조건에 맞는 대화 세트를 {batch_size}개 생성해줘.

조건:
- 성별: {gender}
- 상황: {situation}
- 대분류 감정: {category}
- 소분류 감정: {detail}

각 세트는 같은 사람이 상담 대화에서 말하는 발화 1~3개로 구성돼.
- 발화1: 처음 감정을 드러내는 말
- 발화2: 좀 더 구체적으로 상황을 설명하는 말
- 발화3: (50% 확률로 포함) 추가 심경 표현

JSON 배열의 배열로만 응답해. 다른 텍스트 없이 순수 JSON만.
형식: [["발화1", "발화2", "발화3"], ["발화1", "발화2"], ...]"""


def _get_group(situation: str) -> str:
    for group, keywords in CATEGORY_KEYWORD_MAP.items():
        for kw in keywords:
            if kw in situation:
                return group
    return "기타"


def _find_category(detail: str) -> str:
    for cat, details in CATEGORY_TO_DETAIL.items():
        if detail in details:
            return cat
    raise ValueError(f"소분류 '{detail}'의 대분류를 찾을 수 없음")


def analyze_gaps(target: int) -> dict[str, int]:
    with open(TRAIN_PKL, "rb") as f:
        data = pickle.load(f)

    dist = Counter(ID2DETAIL[r["detail_label"]] for r in data)
    gaps = {}
    for detail, count in sorted(dist.items(), key=lambda x: x[1]):
        if count < target:
            gaps[detail] = target - count
    return gaps


async def generate_batch(
    client: AsyncOpenAI,
    gender: str,
    situation: str,
    category: str,
    detail: str,
    batch_size: int,
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    async with semaphore:
        prompt = USER_PROMPT_TEMPLATE.format(
            gender=gender,
            situation=situation,
            category=category,
            detail=detail,
            batch_size=batch_size,
        )

        try:
            resp = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
                max_completion_tokens=1000,
            )
            raw = resp.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            utterances = json.loads(raw)
        except Exception as e:
            print(f"  [err] {gender}/{situation}/{category}/{detail}: {e}")
            return []

        rows = []
        for conv_set in utterances:
            if isinstance(conv_set, str):
                conv_set = [conv_set]
            if not isinstance(conv_set, list):
                continue
            for utt in conv_set:
                if not isinstance(utt, str) or len(utt.strip()) < 5:
                    continue
                text = f"[성별] {gender} [상황] {situation} [발화] {utt.strip()}"
                rows.append({
                    "text":           text,
                    "category_label": CATEGORY2ID[category],
                    "detail_label":   DETAIL2ID[detail],
                    "gender":         gender,
                    "situation":      situation,
                    "group":          _get_group(situation),
                    "is_augmented":   False,
                    "source":         "ai_fill",
                })
        return rows


async def main(target: int):
    gaps = analyze_gaps(target)

    if not gaps:
        print(f"모든 소분류가 이미 {target}개 이상입니다.")
        return

    total_needed = sum(gaps.values())
    print(f"\n=== 보충 대상: {len(gaps)}개 소분류 / 총 {total_needed:,}개 필요 ===")
    for detail, need in sorted(gaps.items(), key=lambda x: -x[1]):
        print(f"  {detail}: +{need:,}개")

    # 1회 호출당 ~5개 발화 생성 (batch_size=3, 세트당 1~3발화)
    # 필요량의 ~1/5 만큼 API 호출
    client = AsyncOpenAI()
    semaphore = asyncio.Semaphore(10)

    tasks = []
    call_plan = []
    for detail, need in gaps.items():
        category = _find_category(detail)
        calls_needed = max(1, need // 5)
        for i in range(calls_needed):
            gender = GENDERS[i % 2]
            situation = SITUATIONS[i % len(SITUATIONS)]
            call_plan.append((gender, situation, category, detail))

    random.shuffle(call_plan)
    est_calls = len(call_plan)
    print(f"\nAPI 호출 예정: {est_calls:,}회")
    print(f"예상 비용: ~${est_calls * 0.0006:.1f}")

    for gender, situation, category, detail in call_plan:
        tasks.append(
            generate_batch(client, gender, situation, category, detail, 3, semaphore)
        )

    print(f"\nGPT 호출 시작... (동시 {semaphore._value}개)")
    start = time.time()
    results = await asyncio.gather(*tasks)
    elapsed = time.time() - start

    all_rows = []
    for batch in results:
        all_rows.extend(batch)

    print(f"\n생성 완료: {len(all_rows):,}개 / {elapsed:.1f}초")

    if not all_rows:
        print("생성된 데이터가 없습니다.")
        return

    # 분포 확인
    det_counter = Counter(ID2DETAIL[r["detail_label"]] for r in all_rows)
    print(f"\n=== 생성 분포 ===")
    for detail, count in sorted(det_counter.items(), key=lambda x: -x[1]):
        orig_gap = gaps.get(detail, 0)
        status = "OK" if count >= orig_gap else f"부족 {orig_gap - count}"
        print(f"  {detail}: {count:,}개 (필요 {orig_gap:,} -> {status})")

    # pkl 저장
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"ai_fill_{len(all_rows)}.pkl"

    random.shuffle(all_rows)
    with open(out_path, "wb") as f:
        pickle.dump(all_rows, f)

    print(f"\n저장: {out_path}")
    print(f"형식: stage1_prep.py 출력과 동일한 pkl (바로 학습에 합류 가능)")

    # 샘플 출력
    print(f"\n=== 샘플 5개 ===")
    for r in random.sample(all_rows, min(5, len(all_rows))):
        print(f"  {r['text']}")
        cat = ID2CATEGORY[r['category_label']]
        det = ID2DETAIL[r['detail_label']]
        print(f"  -> {cat} / {det} / {r['situation']}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="부족 소분류 집중 보충 생성기")
    parser.add_argument("--target", type=int, default=5000,
                        help="소분류별 최소 목표 개수 (기본 5000)")
    args = parser.parse_args()

    asyncio.run(main(args.target))

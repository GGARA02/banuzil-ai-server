# ============================================================
# config/prompts/stage_prompts.py
# EFT 단계별 신호 평가 프롬프트 + stageInstruction 빌더
# v12 HTML buildStageInstruction() + evaluateEFTProgress() 완전 이식
# ============================================================

from dataclasses import dataclass, field
from typing import Optional


# ── 신호 레이블 ────────────────────────────────────────────
SIGNAL_LABELS = {
    # 1단계 신호
    "emotion":         "1차 감정 어휘 직접 사용 (두려움·외로움·무가치함 등)",
    "patternAware":    "자신의 반응 패턴 인식 (내가 왜 이렇게 행동했는지)",
    "otherSide":       "상대 행동의 이면 언급 (상대도 힘들었겠다는 시각)",
    "relationConcern": "관계 자체에 대한 걱정 표현",
    # 2단계 신호 (EFT 원모델 기반: Sue Johnson 핵심 변화 사건)
    "vulnerability":            "취약성 직접 표현 — Step 5 (당신이 멀어질 때 나는 ~ 무서워요)",
    "empathy":                  "상대에 대한 공감적 반응 — Step 6 (상대도 실망시키기 싫어서 숨었겠구나)",
    "withdrawer_reengagement":  "철회자 재관여 — Step 7 (회피하던 파트너가 애착 욕구를 직접 표현하며 관계에 재참여)",
    "blamer_softening":         "비난자 연화 — Step 7 (비난하던 파트너가 부드러워지며 취약성을 노출하고 도움을 요청)",
}

STAGE1_SIGNALS = ["emotion", "patternAware", "otherSide", "relationConcern"]
STAGE2_SIGNALS = ["vulnerability", "empathy", "withdrawer_reengagement", "blamer_softening"]


@dataclass
class SignalState:
    """양측 EFT 진행 신호 누적 상태"""
    f: dict[str, bool] = field(default_factory=lambda: {k: False for k in SIGNAL_LABELS})
    m: dict[str, bool] = field(default_factory=lambda: {k: False for k in SIGNAL_LABELS})

    def merge(self, gender: str, new_signals: dict[str, bool]):
        """새 신호를 기존에 누적. 한 번 True된 신호는 False로 돌아가지 않음"""
        target = self.f if gender == "f" else self.m
        for k, v in new_signals.items():
            if k in target and v is True:
                target[k] = True


def build_stage_instruction(
    is_female: bool,
    eft_stage: int,
    signals: SignalState,
    eft_step: int = 0,   # 현재 9스텝 위치 (0이면 단계 내 자동 결정)
    round_num: int = 1,  # 현재 라운드 번호 (1~2: 공감 집중, 3+: 신호 유도)
) -> str:
    """
    현재 내담자의 단계 지침 + 이번 라운드 유도 목표 문자열 생성.
    EFT 3단계 9스텝 구조 완전 반영.
    v12 HTML buildStageInstruction() 이식 + 9스텝 상세화.
    """
    sig       = signals.f if is_female else signals.m
    other_sig = signals.m if is_female else signals.f

    if eft_stage == 1:
        # Step 1~3 자동 결정 (Step 4 = 사이클 정의로 대체)
        # 라운드 상한: 신호가 빨리 감지돼도 각 스텝을 반드시 거치도록 제한
        keys = STAGE1_SIGNALS
        self_count  = sum(1 for k in keys if sig[k])
        other_count = sum(1 for k in keys if other_sig[k])

        # 신호 기반 스텝
        if self_count == 0 and other_count == 0:
            signal_step = 1
        elif self_count < 2:
            signal_step = 2
        elif self_count < 3:
            signal_step = 3
        else:
            signal_step = 3  # Step 4 제거, 최대 Step 3

        # 라운드 기반 상한 (각 스텝 최소 경험 보장)
        if round_num <= 2:
            round_cap = 1
        elif round_num <= 3:
            round_cap = 2
        else:
            round_cap = 3

        current_step = min(signal_step, round_cap)

        step_descs = {
            1: (
                "Step 1 [동맹 형성 + 초기 평가]: 지금은 신뢰를 쌓는 단계예요. "
                "판단하지 말고 두 사람 모두 안전하다고 느끼게 하십시오. "
                "갈등 상황을 파악하되 누가 옳고 그른지 판단하지 마십시오."
            ),
            2: (
                "Step 2 [부정적 사이클 식별]: 두 사람이 함께 만들어낸 반복 패턴을 인식시키십시오. "
                "'파트너가 문제'가 아니라 '이 반복이 공동의 적'임을 깨닫게 하십시오. "
                "비난·분노 같은 2차 정서 아래에 있는 1차 정서(두려움·외로움)를 탐색하십시오."
            ),
            3: (
                "Step 3 [1차 정서 접근]: 표면적 감정(분노·비난·냉담) 아래에 숨겨진 "
                "진짜 감정(버림받을 두려움·무가치함·외로움·수치심)을 조심스럽게 탐색하십시오. "
                "내담자가 '왜 이렇게 반응했는지'를 스스로 발견하도록 도우십시오."
            ),
        }
        step_desc = step_descs[current_step]

        missing       = [k for k in keys if not sig[k]]
        missing_other = [k for k in keys if not other_sig[k]]
        self_has_one  = any(sig[k] for k in keys)
        other_has_one = any(other_sig[k] for k in keys)
        base = f"EFT 1단계 De-escalation (현재 Step {current_step}): {step_desc}"

        # 라운드 1~2: 공감과 경청에 집중, 신호 압박 없음
        if round_num <= 2:
            goal = (
                "이번 라운드는 내담자의 이야기를 충분히 듣고, "
                "내담자가 사용한 구체적 표현과 상황을 반영하며 공감하는 데 집중하라. "
                "신호를 억지로 끌어내려 하지 마라."
            )
        elif not self_has_one:
            goal = (
                "이번 라운드 유도 방향 (자연스러운 대화 흐름 안에서 아래 신호가 드러날 수 있도록 질문 방향을 잡되, 억지로 끌어내지 마라):\n"
                + "\n".join(f"  · {SIGNAL_LABELS[k]}" for k in missing)
            )
        elif not other_has_one:
            goal = (
                "당신의 신호는 확인되었어요. 자연스러운 대화 안에서 상대방의 신호도 드러날 수 있도록 질문 방향을 잡되, 억지로 끌어내지 마라.\n"
                "  (참고) 상대방 미확인 신호:\n"
                + "\n".join(f"  · {SIGNAL_LABELS[k]}" for k in missing_other)
            )
        elif missing:
            goal = (
                "양측 신호 달성 ✅ — 자연스러운 흐름 안에서 아래 신호도 탐색해보라:\n"
                + "\n".join(f"  · {SIGNAL_LABELS[k]}" for k in missing)
                + "\n→ 사이클 정의로 넘어갈 준비가 되고 있어요."
            )
        else:
            goal = "1단계 신호 모두 확인됨. 사이클 정의로 넘어갈 준비가 되었어요."

    elif eft_stage == 2:
        keys = STAGE2_SIGNALS
        self_count  = sum(1 for k in keys if sig[k])
        other_count = sum(1 for k in keys if other_sig[k])

        # 핵심 변화 사건 확인
        has_reengagement = sig.get("withdrawer_reengagement", False) or other_sig.get("withdrawer_reengagement", False)
        has_softening    = sig.get("blamer_softening", False) or other_sig.get("blamer_softening", False)

        if self_count == 0:
            current_step = 5
            step_desc = (
                "Step 5 [암묵적 욕구·두려움 접근]: 내담자가 말하지 못했던 깊은 애착 욕구와 "
                "'나는 사랑받을 가치가 없다'는 자기 모델을 수면 위로 올리십시오. "
                "Evocative Responding으로 내담자 스스로 발견하게 하십시오."
            )
        elif self_count < 2:
            current_step = 6
            step_desc = (
                "Step 6 [상대방의 수용 촉진]: 파트너가 상대의 취약성을 따뜻하게 받아들이도록 돕습니다. "
                "한쪽이 취약성을 드러낼 때 다른 쪽이 공감으로 반응하는 '교정적 정서 경험'을 이끌어내십시오."
            )
        else:
            current_step = 7
            step_desc = (
                "Step 7 [애착 욕구 표현 구조화 — 핵심 변화 사건]: "
                "인액트먼트(Enactment) — 지금 이 자리에서 파트너에게 애착 욕구를 직접 말하도록 구조화하십시오.\n"
                f"  철회자 재관여(Withdrawer Re-engagement): {'확인됨' if has_reengagement else '미확인 — 회피하던 파트너가 관계에 재참여하도록 유도'}\n"
                f"  비난자 연화(Blamer Softening): {'확인됨' if has_softening else '미확인 — 비난하던 파트너가 취약성을 드러내도록 유도'}"
            )

        missing       = [k for k in keys if not sig[k]]
        missing_other = [k for k in keys if not other_sig[k]]
        base = f"EFT 2단계 Restructuring (현재 Step {current_step}): {step_desc}"

        if missing or missing_other:
            goal = (
                "이번 라운드 유도 목표 (아직 미확인된 2단계 신호):\n"
                + "\n".join(f"  ❌ 당신 — {SIGNAL_LABELS[k]}" for k in missing)
                + (
                    "\n  (참고) 상대방 미확인: "
                    + " / ".join(SIGNAL_LABELS[k] for k in missing_other)
                    if missing_other else ""
                )
                + "\n→ 위 신호 중 이번 질문으로 자연스럽게 이끌어낼 수 있는 방향을 선택하십시오."
            )
        else:
            goal = "2단계 신호 모두 확인됨. 3단계로 전진할 준비를 하십시오."

    else:  # stage == 3
        base = "EFT 3단계 Consolidation (Step 8~9):"
        step_desc = (
            "Step 8 [새로운 해결책 촉진]: 두 사람이 변화된 소통 방식을 일상 문제에 적용하는 "
            "구체적 합의안을 스스로 도출하도록 촉진하십시오. AI는 판단하지 않고 전달 역할만 합니다.\n"
            "Step 9 [안전 애착 공고화]: 두 사람이 새로 만든 안전한 유대감의 이야기를 함께 정리하고 "
            "확인하십시오. 사이클 인식 능력을 강화하여 재발을 예방하십시오."
        )
        goal = (
            f"{step_desc}\n\n"
            "양측 모두 취약성 표현이 확인됐습니다. "
            "새로운 유대를 강화하고 일상 적용을 지지하십시오. "
            "두 내담자가 합의안을 직접 논의하도록 촉진하고, AI는 전달 역할만 수행하십시오."
        )

    return base + "\n\n" + goal


# ── EFT 진행도 평가 프롬프트 ─────────────────────────────
def build_eval_prompt(
    eft_stage: int,
    stage_rounds: dict[int, int],
    signals: SignalState,
    couple_combo: str,
    f_type: str, f_anxiety: float, f_avoidance: float,
    m_type: str, m_anxiety: float, m_avoidance: float,
    round_num: int,
    f_history_summary: str,
    m_history_summary: str,
) -> str:
    """
    라운드 종료 후 EFT 진행도 평가 GPT 호출 프롬프트.
    v12 HTML evaluateEFTProgress() evalPrompt 이식.
    """
    cur_stage_rounds = stage_rounds.get(eft_stage, 0)
    can_advance      = cur_stage_rounds >= 2

    f_sigs = signals.f
    m_sigs = signals.m

    return f"""너는 EFT 커플 상담 진행도 평가 전문가다. 아래 기록을 분석하여 JSON만 출력하라. 마크다운 없이 순수 JSON만.

[단계 전환 규칙]
현재 단계: {eft_stage}단계
현재 단계 누적 소화 라운드: {cur_stage_rounds}라운드 (전진 가능 조건: 2라운드 이상)
전진 가능 여부: {"가능 — 아래 전진 신호 확인" if can_advance else "불가 — 누적 라운드 미충족, 현 단계 유지"}

▶ 1단계→2단계 전진: 별도 사이클 동의 절차로 관리됨. stage를 2로 올리지 마라 (코드가 직접 처리).
▶ 2단계→3단계 전진 (여성·남성 양측 모두에서 아래 중 3가지 이상 확인 시, 전진 가능할 때만):
    - 취약성 직접 표현 (Step 5): "당신이 멀어질 때 혼자가 되는 것 같아서 무서워요" 등
    - 상대에 대한 공감적 반응 (Step 6): "걔도 나를 실망시키기 싫어서 숨었던 거구나" 등
    - 철회자 재관여 (Step 7): 회피하던 파트너가 애착 욕구를 직접 표현하며 관계에 재참여
    - 비난자 연화 (Step 7): 비난하던 파트너가 부드러워지며 취약성을 노출하고 도움을 요청
▶ 단계 후퇴 없음: stage는 현재 단계({eft_stage}) 이상의 값만 반환하라.
▶ 3단계 종료: 별도 동의 버튼으로 관리됨. readyForEnd는 항상 false.

출력 JSON 스키마:
{{
  "stage": {eft_stage}이상의 정수 (1→2 전진은 하지 말 것, 2→3은 신호 기반),
  "progress": 0~100정수,
  "readyForEnd": false고정,
  "reason": "한줄이유",
  "f_attachment_applied": "여성에게 적용된 애착 특수화 한줄요약",
  "m_attachment_applied": "남성에게 적용된 애착 특수화 한줄요약",
  "f_coping": "여성이 자신의 애착유형에 따라 현재 보이는 대처 방식 한줄",
  "m_coping": "남성이 자신의 애착유형에 따라 현재 보이는 대처 방식 한줄",
  "f_signals": {{
    "emotion": true또는false,
    "patternAware": true또는false,
    "otherSide": true또는false,
    "relationConcern": true또는false,
    "vulnerability": true또는false,
    "empathy": true또는false,
    "withdrawer_reengagement": true또는false,
    "blamer_softening": true또는false
  }},
  "m_signals": {{
    "emotion": true또는false,
    "patternAware": true또는false,
    "otherSide": true또는false,
    "relationConcern": true또는false,
    "vulnerability": true또는false,
    "empathy": true또는false,
    "withdrawer_reengagement": true또는false,
    "blamer_softening": true또는false
  }}
}}
신호 판단: 누적 기준. 이전에 확인된 신호는 true 유지.
현재까지 확인된 신호:
여성: emotion={f_sigs['emotion']}, patternAware={f_sigs['patternAware']}, otherSide={f_sigs['otherSide']}, relationConcern={f_sigs['relationConcern']}, vulnerability={f_sigs['vulnerability']}, empathy={f_sigs['empathy']}, withdrawer_reengagement={f_sigs['withdrawer_reengagement']}, blamer_softening={f_sigs['blamer_softening']}
남성: emotion={m_sigs['emotion']}, patternAware={m_sigs['patternAware']}, otherSide={m_sigs['otherSide']}, relationConcern={m_sigs['relationConcern']}, vulnerability={m_sigs['vulnerability']}, empathy={m_sigs['empathy']}, withdrawer_reengagement={m_sigs['withdrawer_reengagement']}, blamer_softening={m_sigs['blamer_softening']}

커플 분류: {couple_combo}
여성: {f_type} (불안 {f_anxiety:.2f}, 회피 {f_avoidance:.2f})
남성: {m_type} (불안 {m_anxiety:.2f}, 회피 {m_avoidance:.2f})
현재 라운드: {round_num} / 현재 단계: {eft_stage}단계 / 현재 단계 누적: {cur_stage_rounds}라운드

[여성 상담 기록]
{f_history_summary}

[남성 상담 기록]
{m_history_summary}"""


# ── 총알잡기 감지 프롬프트 ─────────────────────────────────
# DSPy 최적화 파일 없을 때 기본 프롬프트로 사용됨
def build_bullet_detect_prompt(
    is_female: bool,
    eft_stage: int,
    reply: str,
) -> str:
    """발화에서 총알(Bullet) 유형 감지"""
    speaker = "여성" if is_female else "남성"
    return f"""너는 EFT 커플 상담 중 발생하는 방어적·공격적 발화(총알, Bullet)를 감지하는 분류기다. 아래 발화를 분석하여 JSON만 출력하라.

[총알 유형 정의]
- Reactive: 1단계 방어기제. 상대방 개인에게 비난·비판·공격. 감정이 격렬하고 직설적.
- Mistrust: 2단계 방어기제. 상대의 새로운 모습에 혼란·불신. "적이 낯선 사람이 되는" 현상. 부드럽지만 저항적.
- None: 총알 아님. 일반적 대화 또는 취약성 표현.

[현재 상담 단계] {eft_stage}단계
[발화자] {speaker}
[발화 내용]
{reply}

출력 JSON:
{{
  "bullet_detected": true또는false,
  "bullet_type": "Reactive" 또는 "Mistrust" 또는 "None",
  "confidence": 0.0~1.0,
  "reason": "한줄이유",
  "suggested_intervention": "Politely Blocking / Closed Validation / Open Validation & Reframing / Parts Talk / Reality Check & Organizing 중 가장 적합한 개입 방식"
}}"""

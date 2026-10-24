"""
المُحكِّم (Arbiter) — حسم تعارض قرارات الوكلاء بقواعد صريحة
=========================================================
عندما يقترح وكيلان خيارين مختلفين للقرار نفسه، لا نترك «الأعلى صوتاً» يفوز،
ولا نأخذ المتوسط. ثلاث قواعد مرتّبة، وكل حكم يسجّل أي قاعدة طبّقها ولماذا:

  1. **مالك واحد لكل قرار**: إن كان للقرار مالك مُعلَن واقترح، فاقتراحه هو الحكم.
  2. **الدليل قبل الثقة**: بلا مالك ⇒ يفوز الاقتراح الذي يحمل أدلة أكثر؛ عند
     التعادل في الأدلة تُقارن الثقة — لكن بفارق لا يقل عن `MIN_MARGIN`.
  3. **التعادل ⇒ تصعيد**: لا قرار آلي عند تقارب الاقتراحات؛ يُرفع للإنسان.

كل حكم (`Ruling`) يُمرَّر إلى `recorder` (إن وُجد) ليُحفظ في الذاكرة — فيبقى
«سبب الترجيح» قابلاً للاسترجاع في المرة القادمة، وهذا هو المطلوب في الدفعة.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, Field

MIN_MARGIN = 0.15  # أقل فارق ثقة يُعدّ ترجيحاً لا ضوضاء

RulingKind = Literal["owner", "evidence", "confidence", "escalate"]


class Proposal(BaseModel):
    """اقتراح وكيل واحد لقرار محدد."""

    agent: str
    decision_key: str
    choice: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    rationale: str = ""


class Ruling(BaseModel):
    """حكم المُحكِّم — مع القاعدة المطبَّقة والسبب المكتوب."""

    decision_key: str
    choice: str | None
    winner: str | None
    rule: RulingKind
    reason: str
    considered: list[str] = Field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return self.rule == "escalate"

    def render(self) -> str:
        head = f"[{self.decision_key}] "
        if self.escalated:
            return head + f"تصعيد — {self.reason}"
        return head + f"«{self.choice}» من {self.winner} (قاعدة: {self.rule}) — {self.reason}"


class ConflictError(ValueError):
    """اقتراحات لمفاتيح قرار مختلفة مرّرت للحكم نفسه."""


class Arbiter:
    """
    يحسم بين الاقتراحات. `owners` يربط كل مفتاح قرار بوكيل مالك واحد.

    `recorder` اختياري: دالة تستقبل النص المُرحَّل للذاكرة (مثلاً `MemoryStore.add`).
    """

    def __init__(
        self,
        owners: dict[str, str] | None = None,
        recorder: Callable[[str], object] | None = None,
        min_margin: float = MIN_MARGIN,
    ) -> None:
        self.owners = dict(owners or {})
        self.recorder = recorder
        self.min_margin = float(min_margin)
        self.history: list[Ruling] = []

    def resolve(self, proposals: Sequence[Proposal]) -> Ruling:
        if not proposals:
            raise ValueError("لا اقتراحات للحسم.")
        keys = {item.decision_key for item in proposals}
        if len(keys) != 1:
            raise ConflictError(f"اقتراحات لمفاتيح مختلفة: {sorted(keys)}")
        key = next(iter(keys))
        considered = [f"{item.agent}→{item.choice}({item.confidence:.2f}, أدلة {len(item.evidence)})" for item in proposals]

        # إجماع: لا حاجة لحكم.
        choices = {item.choice for item in proposals}
        if len(choices) == 1:
            ruling = Ruling(
                decision_key=key,
                choice=next(iter(choices)),
                winner=",".join(item.agent for item in proposals),
                rule="evidence",
                reason="إجماع كل الاقتراحات.",
                considered=considered,
            )
            return self._finish(ruling)

        # القاعدة 1: المالك.
        owner = self.owners.get(key)
        if owner:
            for item in proposals:
                if item.agent == owner:
                    ruling = Ruling(
                        decision_key=key,
                        choice=item.choice,
                        winner=owner,
                        rule="owner",
                        reason=f"{owner} هو المالك المُعلَن لهذا القرار.",
                        considered=considered,
                    )
                    return self._finish(ruling)

        # القاعدة 2: الدليل ثم الثقة.
        ranked = sorted(proposals, key=lambda item: (len(item.evidence), item.confidence), reverse=True)
        first, second = ranked[0], ranked[1]
        if len(first.evidence) > len(second.evidence):
            ruling = Ruling(
                decision_key=key,
                choice=first.choice,
                winner=first.agent,
                rule="evidence",
                reason=f"أدلة أكثر ({len(first.evidence)} مقابل {len(second.evidence)}).",
                considered=considered,
            )
            return self._finish(ruling)
        if first.confidence - second.confidence >= self.min_margin:
            ruling = Ruling(
                decision_key=key,
                choice=first.choice,
                winner=first.agent,
                rule="confidence",
                reason=(
                    f"تعادل في الأدلة؛ فارق الثقة {first.confidence - second.confidence:.2f} "
                    f"≥ {self.min_margin:.2f}."
                ),
                considered=considered,
            )
            return self._finish(ruling)

        # القاعدة 3: تصعيد.
        ruling = Ruling(
            decision_key=key,
            choice=None,
            winner=None,
            rule="escalate",
            reason=(
                "لا مالك، وتعادل في الأدلة، وفارق الثقة "
                f"{abs(first.confidence - second.confidence):.2f} < {self.min_margin:.2f} — يُرفع للإنسان."
            ),
            considered=considered,
        )
        return self._finish(ruling)

    def _finish(self, ruling: Ruling) -> Ruling:
        self.history.append(ruling)
        if self.recorder is not None:
            self.recorder(ruling.render())
        return ruling

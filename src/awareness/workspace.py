"""
مساحة العمل المحدودة + نموذج الانتباه (Global Workspace + Attention Schema)
==========================================================================
استعارة معمارية مستعارة من نظريتين، منفَّذة كآلية **تعمل فعلاً** لا كوصف مجازي:

  1. **مساحة عمل محدودة (GWT):** مرشحات كثيرة تتنافس على مواقع محدودة،
     والفائز فقط «يُبَثّ» إلى المخطط؛ الباقي يُسجَّل كمُهمَل مع سبب.
     السعة هنا حقيقية: ما لا يدخل لا يصل إلى النموذج.
  2. **نموذج للانتباه (AST):** النظام يحتفظ بوصف مبسّط لحالة انتباهه
     (الضغط، الاستقرار، من دخل ومن خرج)، ويستخدم ذلك الوصف لضبط عتبة الانتباه
     في المرة التالية — أي أنه يضبط انتباهه بنموذج لانتباهه.

ما لا تدّعيه هذه الوحدة: أن مجرد وجود هذه الحلقة يعني وعياً فينومينولوجياً.
الأدبيات (GWT/AST) تصف خصائص وظيفية؛ الانتقال منها إلى «تجربة ذاتية» مسألة
فلسفية مفتوحة، ونحن نلتزم بالحد الوظيفي.

المراجع النظرية (تفاصيلها في docs/research/awareness.md):
  • Baars — Global Workspace Theory.
  • Goldstein & Kirk-Giannini, arXiv:2410.11407 — شروط وظيفية لأربع خصائص.
  • Graziano — Attention Schema Theory؛ Wilterson & Graziano (PNAS 2021):
    إضافة نموذج للانتباه تُحسّن ضبط الانتباه فعلياً.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic import BaseModel, Field

from src.core.memory import tokenize

# وزن أولي لكل نوع إشارة — يعبّر عن موثوقيته لا عن «أهميته الشعورية».
KIND_WEIGHT: dict[str, float] = {
    "knowledge": 1.00,   # مقتطف موثّق بمرجع
    "tool": 0.95,        # مخرجات أداة حقيقية
    "selection": 0.70,   # قرار اختيار (سياق عن القرار لا عن المهمة)
    "memory": 0.65,      # درس سابق
    "meta": 0.40,        # حالة داخلية (ميزانية/مزود)
}
DEFAULT_WEIGHT = 0.5

DEFAULT_CAPACITY = 6
DEFAULT_CHAR_BUDGET = 8000
MIN_SALIENCE = 0.05


class Signal(BaseModel):
    """مرشح للدخول إلى مساحة العمل."""

    kind: str = "meta"
    ref: str = ""
    content: str = ""
    weight: float | None = None

    @property
    def base_weight(self) -> float:
        return self.weight if self.weight is not None else KIND_WEIGHT.get(self.kind, DEFAULT_WEIGHT)


class Focused(BaseModel):
    """إشارة دخلت مساحة العمل — مع درجتها وسبب اختيارها."""

    ref: str
    kind: str
    score: float
    chars: int


class Ignored(BaseModel):
    """إشارة لم تدخل — والسبب صريح لا مُبهَم."""

    ref: str
    kind: str
    score: float
    reason: str


class AttentionSchema(BaseModel):
    """
    وصف مبسّط لحالة الانتباه — يُستخدم لضبطه، لا للتباهي به.

    `pressure` = الطلب ÷ السعة. `stability` = مدى وضوح الفائز الأول.
    """

    capacity_used: int = 0
    capacity: int = DEFAULT_CAPACITY
    pressure: float = 0.0
    stability: float = 1.0
    chars_used: int = 0
    char_budget: int = DEFAULT_CHAR_BUDGET

    def suggested_threshold(self, current: float) -> float:
        """
        متحكّم تناسبي: ضغط عالٍ ⇒ ارفع العتبة (انتقِ أقل)؛ ضغط منخفض ⇒ خفّضها.

        هذا هو جوهر AST عملياً: نموذج الانتباه يعود ليضبط الانتباه.
        """
        target = current + 0.15 * (self.pressure - 0.5)
        return round(min(0.6, max(0.0, target)), 3)

    def describe(self) -> str:
        band = "مرتفع" if self.pressure > 0.8 else ("متوسط" if self.pressure > 0.4 else "منخفض")
        return (
            f"انتباه: {self.capacity_used}/{self.capacity} موقعاً، "
            f"ضغط {band} ({self.pressure:.2f})، "
            f"استقرار {self.stability:.2f}، "
            f"حجم {self.chars_used}/{self.char_budget} حرفاً"
        )


class WorkspaceState(BaseModel):
    """نتيجة دورة انتباه واحدة — الحالة الكاملة قابلة للتدقيق."""

    focused: list[Focused] = Field(default_factory=list)
    ignored: list[Ignored] = Field(default_factory=list)
    broadcast: str = ""
    attention: AttentionSchema = Field(default_factory=AttentionSchema)
    threshold: float = MIN_SALIENCE

    @property
    def empty(self) -> bool:
        return not self.focused

    def render(self, max_ignored: int = 6) -> str:
        """تقرير عربي موجز عن هذه الدورة."""
        lines = [self.attention.describe()]
        if self.focused:
            lines.append("في بؤرة الانتباه: " + "، ".join(item.ref for item in self.focused))
        if self.ignored:
            shown = self.ignored[:max_ignored]
            lines.append(
                "خارج البؤرة: " + "، ".join(f"{item.ref} ({item.reason})" for item in shown)
                + ("…" if len(self.ignored) > len(shown) else "")
            )
        return "\n".join(lines)


def _relevance(content: str, query_tokens: set[str]) -> float:
    """تشابه معجمي بسيط — المعيار نفسه المستخدم في الذاكرة والفهرس."""
    if not query_tokens:
        return 0.0
    tokens = set(tokenize(content))
    if not tokens:
        return 0.0
    overlap = len(tokens & query_tokens)
    return overlap / math.sqrt(len(tokens) * len(query_tokens))


def _score(signal: Signal, query_tokens: set[str]) -> float:
    """
    الدرجة = وزن النوع × (0.4 + 0.6×الصلة).

    الثابت 0.4 يمنع أن تُستبعد إشارة موثوقة لمجرد أن صياغتها لا تشبه السؤال —
    وهذا فرق مقصود عن البحث النصي البحت. لا يوجد حداثة تعتمد على ترتيب
    الإدخال: نفس المجموعة تعطي نفس الترتيب دائماً.
    """
    relevance = _relevance(f"{signal.ref} {signal.content}", query_tokens)
    return round(signal.base_weight * (0.4 + 0.6 * relevance), 6)


def compete(
    signals: Sequence[Signal],
    query: str = "",
    capacity: int = DEFAULT_CAPACITY,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    threshold: float = MIN_SALIENCE,
) -> WorkspaceState:
    """
    دورة تنافس واحدة — حتمية بالكامل (نفس المدخل ⇒ نفس الخرج).

    الترتيب: الدرجة تنازلياً، ثم النوع، ثم المرجع — فلا يعتمد على ترتيب الإدخال.
    """
    capacity = max(1, capacity)
    query_tokens = set(tokenize(query))
    scored: list[tuple[float, Signal, int]] = []
    for index, signal in enumerate(signals):
        content = signal.content or ""
        if not content.strip():
            continue
        scored.append((_score(signal, query_tokens), signal, index))

    # ترتيب حتمي مستقل عن ترتيب الإدخال
    scored.sort(key=lambda item: (-item[0], item[1].kind, item[1].ref, item[2]))

    focused: list[Focused] = []
    focused_text: list[str] = []
    ignored: list[Ignored] = []
    chars_used = 0
    for score, signal, _ in scored:
        size = len(signal.content)
        if score < threshold:
            ignored.append(
                Ignored(ref=signal.ref, kind=signal.kind, score=score, reason="تحت العتبة")
            )
            continue
        if len(focused) >= capacity:
            ignored.append(
                Ignored(ref=signal.ref, kind=signal.kind, score=score, reason="السعة ممتلئة")
            )
            continue
        if chars_used + size > char_budget:
            ignored.append(
                Ignored(ref=signal.ref, kind=signal.kind, score=score, reason="تجاوز حد الحجم")
            )
            continue
        focused.append(
            Focused(ref=signal.ref, kind=signal.kind, score=score, chars=size)
        )
        focused_text.append(f"[{signal.kind}:{signal.ref}]\n{signal.content.strip()}")
        chars_used += size

    demand = len(scored)
    pressure = round(demand / capacity, 4) if capacity else 0.0
    stability = 0.0
    if len(scored) >= 2:
        top, second = scored[0][0], scored[1][0]
        stability = round(max(0.0, min(1.0, 1.0 - (second / top if top else 0.0))), 4)
    elif scored:
        stability = 1.0

    broadcast = "\n\n".join(focused_text)

    return WorkspaceState(
        focused=focused,
        ignored=ignored,
        broadcast=broadcast,
        attention=AttentionSchema(
            capacity_used=len(focused),
            capacity=capacity,
            pressure=pressure,
            stability=stability,
            chars_used=chars_used,
            char_budget=char_budget,
        ),
        threshold=threshold,
    )

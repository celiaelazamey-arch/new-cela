"""
التقييم المعرفي (Metacognition) — ثقة مُعايَرة لا مُتفائلة
========================================================
«ما مدى ثقتي في هذه النتيجة؟» سؤال يخطئ فيه الوكلاء كثيراً: يقدّمون مخرجاً
واثقاً بينما الدليل ناقص. هنا تُحسب الثقة من **أدلة قابلة للعدّ** فقط:

  • هل تحققت الشروط الإلزامية؟ (الحاكم الأقوى)
  • نسبة الشروط المجتازة، وتنوّع أنواع الأدلة (أداة/خطوة/معرفة/اختيار).
  • هل فشلت أداة أو مُنع نداء؟ (خصم صريح)
  • هل المزود مجاني مُتحقَّق منه؟ (خصم عند الشك)

قواعد صلبة:
  1. الثقة **لا تبلغ 1.0 أبداً**: لا شيء في هذا النظام يبرّر يقيناً مطلقاً.
  2. بلا تحقق ⇒ الثقة منخفضة حتى لو كان النص يبدو مقنعاً.
  3. كل خصم وكل إضافة له سبب مكتوب (`drivers`)، فلا رقم بلا تفسير.
  4. الثقة **ليست** احتمال صحة؛ هي مؤشر داخلي مبني على اكتمال الأدلة.
"""
from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

MAX_CONFIDENCE = 0.9
MIN_CONFIDENCE = 0.05


class ConfidenceEstimate(BaseModel):
    """تقدير واحد مع تفصيل مسوّغاته — قابل للمراجعة سطراً سطراً."""

    score: float
    band: str
    drivers: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    @property
    def is_low(self) -> bool:
        return self.score < 0.5

    def render(self) -> str:
        head = f"الثقة: {self.score:.2f} ({self.band})"
        drivers = "؛ ".join(self.drivers) if self.drivers else "لا مسوّغات"
        caveats = "؛ ".join(self.caveats) if self.caveats else "لا تحفّظات مسجّلة"
        return f"{head}\n  + {drivers}\n  - {caveats}"


def band_of(score: float) -> str:
    if score >= 0.75:
        return "مرتفعة مشروطة"
    if score >= 0.5:
        return "متوسطة"
    if score >= 0.25:
        return "منخفضة"
    return "ضعيفة جداً"


def estimate(
    *,
    verified: bool,
    checks: Sequence[object] = (),
    evidence: Sequence[object] = (),
    tool_failures: int = 0,
    denied_tools: int = 0,
    provider_unverified: bool = False,
    truncated_context: bool = False,
) -> ConfidenceEstimate:
    """
    يحسب الثقة من إشارات صريحة فقط.

    `checks` و`evidence` تُقرأ عبر سمات (`passed`/`kind`/`ok`) لتقبل كائنات
    المحرك دون استيراد دائري.
    """
    score = 0.30
    drivers: list[str] = []
    caveats: list[str] = []

    passed = [check for check in checks if getattr(check, "passed", False)]
    required = [check for check in checks if getattr(check, "required", True)]
    total_required = len(required) or len(checks)

    if verified:
        score += 0.35
        drivers.append("اجتازت المهمة كل شروط التحقق المطلوبة (+0.35)")
    elif checks:
        caveats.append("لم تتحقق كل الشروط — الثقة لا تتجاوز المتوسط")

    if total_required:
        ratio = len(passed) / total_required
        bonus = round(0.20 * ratio, 3)
        score += bonus
        drivers.append(f"اجتياز الشروط {len(passed)}/{total_required} (+{bonus:.2f})")

    kinds = {getattr(item, "kind", "") for item in evidence if getattr(item, "ok", True)}
    kinds.discard("")
    if len(kinds) >= 3:
        score += 0.10
        drivers.append(f"تنوّع الأدلة ({len(kinds)} أنواع) (+0.10)")
    elif kinds:
        caveats.append(f"أنواع الأدلة محدودة ({', '.join(sorted(kinds))})")

    if not evidence:
        score -= 0.15
        caveats.append("لا دليل مسجّل (−0.15)")

    if tool_failures:
        penalty = min(0.20, 0.05 * tool_failures)
        score -= penalty
        caveats.append(f"{tool_failures} أداة فشلت (−{penalty:.2f})")

    if denied_tools:
        penalty = min(0.15, 0.05 * denied_tools)
        score -= penalty
        caveats.append(f"{denied_tools} نداء مُنع بالسياسة (−{penalty:.2f})")

    if provider_unverified:
        score -= 0.10
        caveats.append("مزود غير مُتحقَّق من مجانيته (−0.10)")

    if truncated_context:
        score -= 0.05
        caveats.append("سياق مُقتطع بسبب سعة الانتباه (−0.05)")

    bounded = round(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, score)), 2)
    if bounded >= MAX_CONFIDENCE:
        caveats.append("الثقة محدودة بسقف 0.90 — لا يقين مطلق في هذا النظام")

    return ConfidenceEstimate(
        score=bounded,
        band=band_of(bounded),
        drivers=drivers,
        caveats=caveats,
    )

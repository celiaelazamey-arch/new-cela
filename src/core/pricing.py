"""
هندسة الخطط والتسعير وحماية الهامش (Pricing, Quotas & Unit Economics)
====================================================================
تُجسّد هذه الوحدة البنود الهندسية من خطة الربح والتسعير (`docs/monetization-plan.md`):

  • البند 3 و4 (هيكل الخطط ونماذج التسعير):
    خطط محددة الحصص (`FREE`, `PRO`, `TEAM`, `BUSINESS`, `ENTERPRISE`) بنموذج Hybrid
    (اشتراك شهري/سنوي بخصم 15–20% + مقاعد للفرق + سقف صارم للمهام ونداءات النماذج).
    **لا توجد خطة «غير محدودة» على تكلفة الذكاء الاصطناعي المتغيرة.**

  • البند 5 (حماية الهامش — Margin Protection):
    1) ربط كل خطة بـ `QuotaGuard` مع بقاء `allow_paid=False` افتراضياً (الخط الأحمر).
    2) تصنيف تعقيد المهمة (`TaskComplexity`) لتوجيه المهام البسيطة إلى نماذج سريعة/منخفضة
       التكلفة والمهام المعقدة إلى نماذج الاستدلال العميق.
    3) حاسبة اقتصاديات الوحدة (`calculate_unit_economics`) لحساب تكلفة كل مستخدم
       (Inference + تخزين + دعم) وهامش الربح الإجمالي ونسبة `LTV / CAC`.

  • البند 7 (التحويل من مجاني لمدفوع — تنبيه 80% + Paywall عند لحظة القيمة):
    دالة `evaluate_usage_alert` تراقب استهلاك `QuotaGuard` وتُصدر تنبيهاً عربياً واضحاً
    عند الوصول إلى 80% من الحد، وتُفعّل بوابة الترقية عند بلوغ 100%.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.core.quota import QuotaGuard

# عتبة التنبيه المبكر قبل نفاد الحصة (البند 7: رسائل عند الوصول لـ 80% من الحد)
DEFAULT_WARN_THRESHOLD: float = 0.80

# الحد الأدنى الآمن لهامش الربح الإجمالي في شركات AI SaaS (البند 5 و11)
MIN_HEALTHY_MARGIN_PCT: float = 70.0

# الهدف القياسي لنسبة القيمة الدائمة إلى تكلفة الاكتساب (البند 11: LTV > 3x CAC)
MIN_HEALTHY_LTV_CAC: float = 3.0


class PlanTier(str, Enum):
    """شرائح الخطط المعتمدة في النظام (البند 3)."""

    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class BillingCycle(str, Enum):
    """دورة الفوترة (البند 4: اشتراك شهري أو سنوي بخصم احتفاظ)."""

    MONTHLY = "monthly"
    ANNUAL = "annual"


class TaskComplexity(str, Enum):
    """مستوى تعقيد المهمة لتوجيه النموذج الأرخص أو الأقوى (البند 5)."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass(frozen=True)
class PlanSpec:
    """
    بطاقة مواصفة الخطة التسعيرية والتشغيلية.

    ملاحظة معمارية: جميع الخطط تفرض سقفاً رقمياً صريحاً (`monthly_tasks_quota`،
    `max_model_calls_per_task`، `max_tool_calls_per_task`) منعاً لتآكل الهامش
    بسبب التكلفة المتغيرة للنماذج (البند 4 و13).
    """

    tier: PlanTier
    name_ar: str
    target_segment: str
    monthly_price_egp: int
    monthly_price_usd: int
    annual_discount_pct: int
    included_seats: int
    extra_seat_egp: int
    extra_seat_usd: int
    monthly_tasks_quota: int
    max_model_calls_per_task: int
    max_tool_calls_per_task: int
    memory_capacity: int
    ocr_pages_monthly: int
    watermark_reports: bool
    rbac_and_sso: bool
    audit_retention_days: int
    sla_uptime_pct: float | None
    highlights: tuple[str, ...]

    def effective_monthly_egp(
        self,
        cycle: BillingCycle = BillingCycle.MONTHLY,
        seats: int | None = None,
    ) -> int:
        """السعر الشهري الفعلي بالجنيه المصري بعد حساب المقاعد والخصم السنوي."""
        total_seats = max(self.included_seats, int(seats or self.included_seats))
        extra_seats = max(0, total_seats - self.included_seats)
        base = self.monthly_price_egp + (extra_seats * self.extra_seat_egp)
        if cycle is BillingCycle.ANNUAL and self.annual_discount_pct > 0:
            return round(base * (100 - self.annual_discount_pct) / 100)
        return base

    def effective_monthly_usd(
        self,
        cycle: BillingCycle = BillingCycle.MONTHLY,
        seats: int | None = None,
    ) -> int:
        """السعر الشهري الفعلي بالدولار بعد حساب المقاعد والخصم السنوي."""
        total_seats = max(self.included_seats, int(seats or self.included_seats))
        extra_seats = max(0, total_seats - self.included_seats)
        base = self.monthly_price_usd + (extra_seats * self.extra_seat_usd)
        if cycle is BillingCycle.ANNUAL and self.annual_discount_pct > 0:
            return round(base * (100 - self.annual_discount_pct) / 100)
        return base

    def annual_total_egp(self, seats: int | None = None) -> int:
        """إجمالي الفاتورة السنوية بالجنيه المصري."""
        return self.effective_monthly_egp(BillingCycle.ANNUAL, seats=seats) * 12

    def annual_total_usd(self, seats: int | None = None) -> int:
        """إجمالي الفاتورة السنوية بالدولار."""
        return self.effective_monthly_usd(BillingCycle.ANNUAL, seats=seats) * 12


# سجل الخطط المعتمد لنظام new-cela (البنود 2، 3، 4)
PLAN_CATALOG: dict[PlanTier, PlanSpec] = {
    PlanTier.FREE: PlanSpec(
        tier=PlanTier.FREE,
        name_ar="المجانية (Free)",
        target_segment="أفراد (طلاب، مطورون، تجربة أولية للاكتساب)",
        monthly_price_egp=0,
        monthly_price_usd=0,
        annual_discount_pct=0,
        included_seats=1,
        extra_seat_egp=0,
        extra_seat_usd=0,
        monthly_tasks_quota=30,
        max_model_calls_per_task=40,
        max_tool_calls_per_task=20,
        memory_capacity=100,
        ocr_pages_monthly=25,
        watermark_reports=True,
        rbac_and_sso=False,
        audit_retention_days=7,
        sla_uptime_pct=None,
        highlights=(
            "وكيل تحقق بـ SHA-256 مع 7 بوابات فحص آلي",
            "توجيه مجاني متعدد المزودات (Gemini / Groq) أو BYOK",
            "علامة مائية على التقارير المصدّرة (للاكتساب والانتشار)",
        ),
    ),
    PlanTier.PRO: PlanSpec(
        tier=PlanTier.PRO,
        name_ar="المحترف (Pro)",
        target_segment="مستقلون (Freelancers)، مطورون أوائل، وباحثون يدفعون لتسريع عملهم",
        monthly_price_egp=490,
        monthly_price_usd=12,
        annual_discount_pct=17,
        included_seats=1,
        extra_seat_egp=0,
        extra_seat_usd=0,
        monthly_tasks_quota=300,
        max_model_calls_per_task=120,
        max_tool_calls_per_task=60,
        memory_capacity=500,
        ocr_pages_monthly=500,
        watermark_reports=False,
        rbac_and_sso=False,
        audit_retention_days=30,
        sla_uptime_pct=None,
        highlights=(
            "الاستخدام الكامل للحلقة المعرفية (Cognitive Repair Loop)",
            "فهرسة Google Drive + استخراج OCR حتى 500 صفحة/شهر باقتباسات [#L]",
            "تصدير تقارير بدون علامة ترويجية + أولوية تنفيذ",
        ),
    ),
    PlanTier.TEAM: PlanSpec(
        tier=PlanTier.TEAM,
        name_ar="الفرق والشركات الصغيرة (Team)",
        target_segment="شركات برمجيات ناشئة، مكاتب استشارية/قانونية/محاسبية، وورش رقمية (3-10 مستخدمين)",
        monthly_price_egp=1990,
        monthly_price_usd=49,
        annual_discount_pct=20,
        included_seats=5,
        extra_seat_egp=350,
        extra_seat_usd=9,
        monthly_tasks_quota=1500,
        max_model_calls_per_task=250,
        max_tool_calls_per_task=120,
        memory_capacity=2000,
        ocr_pages_monthly=2500,
        watermark_reports=False,
        rbac_and_sso=False,
        audit_retention_days=90,
        sla_uptime_pct=99.0,
        highlights=(
            "5 مقاعد مشمولة + مشاركة فهرس المعرفة والذاكرة الاستراتيجية",
            "مزامنة Google Drive ثنائية الاتجاه بموافقة وكشف تعارض",
            "سجل نشاط وتدقيق + فحص امتثال قانون حماية البيانات المصري 151/2020",
        ),
    ),
    PlanTier.BUSINESS: PlanSpec(
        tier=PlanTier.BUSINESS,
        name_ar="الأعمال (Business)",
        target_segment="شركات متوسطة تحتاج صلاحيات دقيقة وامتثالاً مؤسسياً (10-50 مستخدماً)",
        monthly_price_egp=7500,
        monthly_price_usd=179,
        annual_discount_pct=20,
        included_seats=15,
        extra_seat_egp=450,
        extra_seat_usd=12,
        monthly_tasks_quota=6000,
        max_model_calls_per_task=400,
        max_tool_calls_per_task=200,
        memory_capacity=10000,
        ocr_pages_monthly=10000,
        watermark_reports=False,
        rbac_and_sso=True,
        audit_retention_days=365,
        sla_uptime_pct=99.5,
        highlights=(
            "تسجيل دخول موحّد (SSO) + صلاحيات أدوار (RBAC) + سجل تدقيق سنة كاملة",
            "ربط خوادم MCP مخصّصة داخل بيئة معزولة + تقارير SBOM وCodeQL",
            "اتفاقية مستوى خدمة (SLA 99.5%) + اتفاقية معالجة بيانات (DPA)",
        ),
    ),
    PlanTier.ENTERPRISE: PlanSpec(
        tier=PlanTier.ENTERPRISE,
        name_ar="المؤسسات الكبرى (Enterprise)",
        target_segment="بنوك، شركات كبرى، وجهات تتطلب نشراً خاصاً وعقوداً سنوية مخصّصة",
        monthly_price_egp=25000,
        monthly_price_usd=599,
        annual_discount_pct=20,
        included_seats=50,
        extra_seat_egp=400,
        extra_seat_usd=10,
        monthly_tasks_quota=25000,
        max_model_calls_per_task=800,
        max_tool_calls_per_task=400,
        memory_capacity=50000,
        ocr_pages_monthly=50000,
        watermark_reports=False,
        rbac_and_sso=True,
        audit_retention_days=1825,
        sla_uptime_pct=99.9,
        highlights=(
            "نشر خاص (On-Premise / Private VPC) داخل مصر لمنع النقل العابر للحدود",
            "تكاملات مخصّصة + دعم هندسي مخصّص + تدريب الفرق",
            "امتثال كامل لقانون 151/2020 وEU AI Act مع سجل أدلة غير قابل للتلاعب",
        ),
    ),
}

_TIER_ORDER: tuple[PlanTier, ...] = (
    PlanTier.FREE,
    PlanTier.PRO,
    PlanTier.TEAM,
    PlanTier.BUSINESS,
    PlanTier.ENTERPRISE,
)


def _resolve_tier(tier: PlanTier | str) -> PlanTier:
    """يحوّل القيمة التعدادية أو النصية إلى PlanTier بأمان في Python 3.11+."""
    if isinstance(tier, PlanTier):
        return tier
    return PlanTier(str(tier).strip().lower())


def get_plan(tier: PlanTier | str) -> PlanSpec:
    """يسترجع مواصفة الخطة بالاسم أو بالقيمة التعدادية."""
    return PLAN_CATALOG[_resolve_tier(tier)]


def next_upgrade_tier(tier: PlanTier | str) -> PlanTier | None:
    """يعيد الخطة الأعلى مباشرةً لاقتراحها عند الاقتراب من الحد أو الـ Paywall."""
    current = _resolve_tier(tier)
    idx = _TIER_ORDER.index(current)
    if idx + 1 < len(_TIER_ORDER):
        return _TIER_ORDER[idx + 1]
    return None


def build_plan_guard(
    tier: PlanTier | str = PlanTier.FREE,
    *,
    allow_unverified: bool = False,
    allow_paid: bool = False,
) -> QuotaGuard:
    """
    ينشئ `QuotaGuard` مضبوطاً على حدود الخطة المحددة.

    قاعدة أمان ملزمة (CLAUDE.md §5): يبقى `allow_paid=False` افتراضياً دائماً
    مهما كانت الخطة، ولا يُفتح مزود مدفوع إلا بتمرير صريح بعد مراجعة بشرية.
    """
    spec = get_plan(tier)
    return QuotaGuard(
        max_model_calls=spec.max_model_calls_per_task,
        max_tool_calls=spec.max_tool_calls_per_task,
        allow_unverified=allow_unverified,
        allow_paid=allow_paid,
    )


# ---------------- البند 7: تنبيه 80% + Paywall عند لحظة القيمة ----------------


@dataclass(frozen=True)
class UsageAlert:
    """نتيجة فحص اقتراب المستخدم من حدود خطته الحالية."""

    tier: PlanTier
    status: str  # "ok" | "warning_80" | "paywall"
    model_usage_ratio: float
    tool_usage_ratio: float
    near_limit: bool
    paywall_triggered: bool
    recommended_tier: PlanTier | None
    messages: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier.value,
            "status": self.status,
            "model_usage_ratio": round(self.model_usage_ratio, 4),
            "tool_usage_ratio": round(self.tool_usage_ratio, 4),
            "near_limit": self.near_limit,
            "paywall_triggered": self.paywall_triggered,
            "recommended_tier": self.recommended_tier.value if self.recommended_tier else None,
            "messages": list(self.messages),
        }


def evaluate_usage_alert(
    guard: QuotaGuard,
    tier: PlanTier | str = PlanTier.FREE,
    *,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
) -> UsageAlert:
    """
    يقيّم استهلاك المهمة مقابل سقف الخطة ويُصدر رسائل التحويل (البند 7):
      - عند الوصول إلى 80% من الحد: رسالة تنبيه مبكر مع اقتراح الخطة الأعلى.
      - عند الوصول إلى 100% من الحد: تفعيل Paywall عند لحظة القيمة مع عرض التجربة.
    """
    if not (0.0 < warn_threshold <= 1.0):
        raise ValueError("warn_threshold يجب أن يكون بين 0 و 1.")

    resolved_tier = _resolve_tier(tier)
    upgrade = next_upgrade_tier(resolved_tier)
    upgrade_spec = get_plan(upgrade) if upgrade else None

    model_ratio = (
        guard.usage.model_calls / guard.max_model_calls
        if guard.max_model_calls > 0
        else 1.0
    )
    tool_ratio = (
        guard.usage.tool_calls / guard.max_tool_calls
        if guard.max_tool_calls > 0
        else 1.0
    )

    paywall = model_ratio >= 1.0 or tool_ratio >= 1.0
    near = (not paywall) and (model_ratio >= warn_threshold or tool_ratio >= warn_threshold)
    messages: list[str] = []

    if paywall:
        status = "paywall"
        if model_ratio >= 1.0:
            messages.append(
                f"🔒 وصلت إلى 100% من حد نداءات النموذج في خطة {get_plan(resolved_tier).name_ar} "
                f"({guard.usage.model_calls}/{guard.max_model_calls})."
            )
        if tool_ratio >= 1.0:
            messages.append(
                f"🔒 وصلت إلى 100% من حد نداءات الأدوات في خطة {get_plan(resolved_tier).name_ar} "
                f"({guard.usage.tool_calls}/{guard.max_tool_calls})."
            )
        if upgrade_spec is not None:
            messages.append(
                f"🚀 فعّل تجربة 14 يوماً لخطة {upgrade_spec.name_ar} "
                f"({upgrade_spec.monthly_price_egp} ج.م/شهر أو ${upgrade_spec.monthly_price_usd}/شهر) "
                f"لرفع السقف إلى {upgrade_spec.max_model_calls_per_task} نداء نموذج لكل مهمة."
            )
    elif near:
        status = "warning_80"
        if model_ratio >= warn_threshold:
            pct = round(model_ratio * 100)
            messages.append(
                f"⚠️ استهلكت {pct}% من حد نداءات النموذج "
                f"({guard.usage.model_calls}/{guard.max_model_calls})."
            )
        if tool_ratio >= warn_threshold:
            pct = round(tool_ratio * 100)
            messages.append(
                f"⚠️ استهلكت {pct}% من حد نداءات الأدوات "
                f"({guard.usage.tool_calls}/{guard.max_tool_calls})."
            )
        if upgrade_spec is not None:
            messages.append(
                f"💡 للترقية السلسة دون انقطاع: خطة {upgrade_spec.name_ar} تمنحك "
                f"{upgrade_spec.max_model_calls_per_task} نداء نموذج و"
                f"{upgrade_spec.max_tool_calls_per_task} نداء أداة لكل مهمة."
            )
    else:
        status = "ok"

    return UsageAlert(
        tier=resolved_tier,
        status=status,
        model_usage_ratio=model_ratio,
        tool_usage_ratio=tool_ratio,
        near_limit=near or paywall,
        paywall_triggered=paywall,
        recommended_tier=upgrade,
        messages=tuple(messages),
    )


# ---------------- البند 5: توجيه النماذج حسب صعوبة المهمة ----------------

_COMPLEX_KEYWORDS: tuple[str, ...] = (
    "أصلح",
    "ثغرة",
    "معمارية",
    "خطة",
    "تحليل",
    "codeql",
    "refactor",
    "security",
    "quantum",
    "benchmark",
    "تعارض",
    "امتثال",
)


def classify_task_complexity(
    prompt: str,
    *,
    require_structured: bool = False,
    tool_count: int = 0,
) -> TaskComplexity:
    """
    يصنّف المهمة لتوجيهها إلى نموذج اقتصادي سريع أو نموذج استدلال قوي (البند 5):
      - `SIMPLE`: مهام قصيرة مباشرة بلا إخراج مهيكل ولا أدوات كثيرة ← نموذج رخيص/سريع.
      - `MODERATE`: مهام متوسطة أو تتطلب إخراجاً مهيكلاً بسيطاً.
      - `COMPLEX`: مهام إصلاح كود، تدقيق أمني، تخطيط متعدد الخطوات، أو أدوات ≥ 3.
    """
    cleaned = (prompt or "").strip()
    lowered = cleaned.lower()
    keyword_hits = sum(1 for kw in _COMPLEX_KEYWORDS if kw in lowered)

    if tool_count >= 3 or len(cleaned) >= 500 or keyword_hits >= 2:
        return TaskComplexity.COMPLEX
    if require_structured or tool_count >= 1 or len(cleaned) >= 160 or keyword_hits == 1:
        return TaskComplexity.MODERATE
    return TaskComplexity.SIMPLE


# ---------------- البند 5 و11: اقتصاديات الوحدة وحماية الهامش ----------------


@dataclass(frozen=True)
class UnitEconomics:
    """تقرير تكلفة المستخدم الواحد وهامش الربح ومؤشرات LTV/CAC (البندان 5 و11)."""

    tier: PlanTier
    arpu_usd: float
    inference_cost_usd: float
    storage_cost_usd: float
    support_cost_usd: float
    total_variable_cost_usd: float
    gross_profit_usd: float
    gross_margin_pct: float
    ltv_usd: float
    cac_usd: float
    ltv_to_cac: float
    margin_healthy: bool
    ltv_cac_healthy: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "tier": self.tier.value,
            "arpu_usd": round(self.arpu_usd, 2),
            "inference_cost_usd": round(self.inference_cost_usd, 4),
            "storage_cost_usd": round(self.storage_cost_usd, 4),
            "support_cost_usd": round(self.support_cost_usd, 4),
            "total_variable_cost_usd": round(self.total_variable_cost_usd, 4),
            "gross_profit_usd": round(self.gross_profit_usd, 2),
            "gross_margin_pct": round(self.gross_margin_pct, 2),
            "ltv_usd": round(self.ltv_usd, 2),
            "cac_usd": round(self.cac_usd, 2),
            "ltv_to_cac": round(self.ltv_to_cac, 2),
            "margin_healthy": self.margin_healthy,
            "ltv_cac_healthy": self.ltv_cac_healthy,
        }


def calculate_unit_economics(
    tier: PlanTier | str,
    *,
    avg_tasks_used: int = 120,
    avg_model_calls_per_task: int = 15,
    cost_per_1k_model_calls_usd: float = 0.45,
    storage_mb_per_user: float = 50.0,
    storage_cost_per_gb_usd: float = 0.15,
    support_cost_usd: float = 0.50,
    monthly_churn_rate: float = 0.05,
    cac_usd: float = 25.0,
    cycle: BillingCycle = BillingCycle.MONTHLY,
    seats: int | None = None,
) -> UnitEconomics:
    """
    يحسب تكلفة كل مستخدم/حساب (Inference + تخزين + دعم) وهامش الربح وLTV/CAC.

    تتحقق الدالة من شرطين حاكمين:
      1. هامش الربح الإجمالي ≥ 70% (`MIN_HEALTHY_MARGIN_PCT`).
      2. القيمة الدائمة للعميل `LTV` ≥ 3 أضعاف تكلفة الاكتساب `CAC` (`MIN_HEALTHY_LTV_CAC`).
    """
    spec = get_plan(tier)
    capped_tasks = min(max(0, int(avg_tasks_used)), spec.monthly_tasks_quota)
    capped_calls_per_task = min(
        max(0, int(avg_model_calls_per_task)),
        spec.max_model_calls_per_task,
    )
    total_calls = capped_tasks * capped_calls_per_task

    inference_cost = (total_calls / 1000.0) * max(0.0, float(cost_per_1k_model_calls_usd))
    storage_cost = (max(0.0, float(storage_mb_per_user)) / 1024.0) * max(
        0.0, float(storage_cost_per_gb_usd)
    )
    support = max(0.0, float(support_cost_usd))
    total_cost = inference_cost + storage_cost + support

    arpu = float(spec.effective_monthly_usd(cycle=cycle, seats=seats))
    gross_profit = arpu - total_cost
    gross_margin_pct = (gross_profit / arpu * 100.0) if arpu > 0 else 0.0

    safe_churn = max(0.005, min(1.0, float(monthly_churn_rate)))
    ltv = (gross_profit / safe_churn) if gross_profit > 0 else 0.0
    safe_cac = max(0.01, float(cac_usd))
    ltv_to_cac = ltv / safe_cac if ltv > 0 else 0.0

    return UnitEconomics(
        tier=spec.tier,
        arpu_usd=arpu,
        inference_cost_usd=inference_cost,
        storage_cost_usd=storage_cost,
        support_cost_usd=support,
        total_variable_cost_usd=total_cost,
        gross_profit_usd=gross_profit,
        gross_margin_pct=gross_margin_pct,
        ltv_usd=ltv,
        cac_usd= float(cac_usd),
        ltv_to_cac=ltv_to_cac,
        margin_healthy=(arpu == 0.0 and total_cost == 0.0)
        or (gross_margin_pct >= MIN_HEALTHY_MARGIN_PCT),
        ltv_cac_healthy=ltv_to_cac >= MIN_HEALTHY_LTV_CAC,
    )


def render_pricing_table() -> str:
    """
    يولّد مقارنة واضحة بين الخطط في صفحة واحدة (البند 7).
    تُستخدم في التوثيق وواجهة CLI (`python -m src.main --plans`).
    """
    lines = [
        "| الخطة | السعر الشهري (مصر / دولي) | السنوي (خصم الاحتفاظ) | المقاعد | المهام/شهر | سقف المهمة (نموذج / أداة) | أبرز المزايا |",
        "|---|---|---|---|---|---|---|",
    ]
    for tier in _TIER_ORDER:
        spec = PLAN_CATALOG[tier]
        if spec.monthly_price_egp == 0:
            monthly_str = "0 ج.م / $0"
            annual_str = "مجاناً"
        else:
            monthly_str = f"{spec.monthly_price_egp:,} ج.م / ${spec.monthly_price_usd}"
            ann_m_egp = spec.effective_monthly_egp(BillingCycle.ANNUAL)
            ann_m_usd = spec.effective_monthly_usd(BillingCycle.ANNUAL)
            annual_str = (
                f"{ann_m_egp:,} ج.م/شهر (${ann_m_usd}/شهر) — خصم {spec.annual_discount_pct}%"
            )
        seats_str = (
            f"{spec.included_seats}"
            if spec.extra_seat_egp == 0
            else f"{spec.included_seats} (+{spec.extra_seat_egp} ج.م/مقعد)"
        )
        quota_str = f"{spec.max_model_calls_per_task} نموذج / {spec.max_tool_calls_per_task} أداة"
        lines.append(
            f"| **{spec.name_ar}** | {monthly_str} | {annual_str} | {seats_str} | "
            f"{spec.monthly_tasks_quota:,} | {quota_str} | {spec.highlights[0]} |"
        )
    return "\n".join(lines)

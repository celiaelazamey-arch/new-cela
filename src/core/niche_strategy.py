"""
استراتيجية النيش العمودي ومقياس القرار بعد 90 يوماً (Vertical Niche Strategy & 90-Day Gate)
==========================================================================================
تُجسّد هذه الوحدة البنود السبعة من وثيقة الاستراتيجية التنافسية مقابل المنصات العامة
(`docs/niche-strategy-manus.md`):

  • الحكم الأساسي:
    منافسة مباشرة كمنصة Agent عامة (مثل Manus) ← غير واقعية.
    منافسة في نيش عمودي محدد بالعربية وامتثال محلي ودفع بالجنيه ← ممكنة ومربحة.

  • البند 4 و5 (أين نفوز + الاستراتيجية المقترحة):
    - لا تقل «بديل Manus» أو «وكيل يفعل أي شيء» ← قل «وكيل لـ [مهنة محددة] بالعربية».
    - التركيز على 1–2 مهام عالية القيمة وقابلة للتكرار لكل نيش حتى تتجاوز الموثوقية 85–90%.
    - التسعير بالجنيه المصري والدفع المحلي (InstaPay · Fawry · Paymob) والامتثال لقانون
      حماية البيانات الشخصية المصري رقم 151 لسنة 2020.

  • البند 6 (درء المخاطر):
    - فحص آلي للرسائل التسويقية (`validate_positioning_claim`) يمنع ادعاء «بديل Manus»
      أو «وكيل عام بلا حدود».

  • البند 7 (مقياس القرار بعد 90 يوماً — 90-Day Decision Gate):
    1) هل يدفع 10 مستخدمين فعلياً؟ (`paying_customers >= 10`)
    2) هل نسبة نجاح المهام في النيش فوق 85–90%؟ (`task_success_rate >= 0.85`)
    3) هل هامش الربح لكل مستخدم موجب؟ (`margin_per_user_egp > 0`)
    ← إذا نعم: وسّع (`EXPAND`) | إذا لا: غيّر النيش لا الأدوات (`PIVOT_NICHE_KEEP_TOOLS`).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.core.pricing import PlanTier

# عتبات القرار بعد 90 يوماً (البند 7)
MIN_PAYING_CUSTOMERS_90D: int = 10
MIN_NICHE_SUCCESS_RATE: float = 0.85
EXCELLENT_NICHE_SUCCESS_RATE: float = 0.90


class VerticalNiche(str, Enum):
    """النيشات العمودية المعتمدة حيث يتفوق `new-cela` محلياً (البند 4)."""

    PRINT_PREPRESS = "print_prepress"
    ACCOUNTING_COMPLIANCE = "accounting_compliance"
    ARABIC_ECOMMERCE = "arabic_ecommerce"
    DEVSECOPS_ARABIC = "devsecops_arabic"


class LocalPaymentMethod(str, Enum):
    """وسائل الدفع المحلية بالجنيه المصري (البند 4)."""

    INSTAPAY = "instapay"
    FAWRY = "fawry"
    PAYMOB_CARD = "paymob_card"
    VODAFONE_CASH = "vodafone_cash"


class NinetyDayDecision(str, Enum):
    """نتيجة مقياس القرار بعد 90 يوماً (البند 7)."""

    EXPAND = "expand"
    PIVOT_NICHE_KEEP_TOOLS = "pivot_niche_keep_tools"
    FIX_WORKFLOW_RELIABILITY = "fix_workflow_reliability"
    FIX_UNIT_ECONOMICS = "fix_unit_economics"


@dataclass(frozen=True)
class NicheWorkflowSpec:
    """
    بطاقة توصيف نيش عمودي محدد (البند 4 و5):
    تركّز على مهنة محددة + 1–2 مهام عالية القيمة + امتثال محلي + دفع بالجنيه.
    """

    niche: VerticalNiche
    title_ar: str
    positioning_statement_ar: str
    target_segment_ar: str
    high_value_workflows: tuple[str, str]
    core_modules_used: tuple[str, ...]
    recommended_plan: PlanTier
    target_success_rate: float
    managed_setup_included: bool
    payment_methods: tuple[LocalPaymentMethod, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "niche": self.niche.value,
            "title_ar": self.title_ar,
            "positioning_statement_ar": self.positioning_statement_ar,
            "target_segment_ar": self.target_segment_ar,
            "high_value_workflows": list(self.high_value_workflows),
            "core_modules_used": list(self.core_modules_used),
            "recommended_plan": self.recommended_plan.value,
            "target_success_rate": self.target_success_rate,
            "managed_setup_included": self.managed_setup_included,
            "payment_methods": [m.value for m in self.payment_methods],
        }


DEFAULT_LOCAL_PAYMENTS: tuple[LocalPaymentMethod, ...] = (
    LocalPaymentMethod.INSTAPAY,
    LocalPaymentMethod.FAWRY,
    LocalPaymentMethod.PAYMOB_CARD,
    LocalPaymentMethod.VODAFONE_CASH,
)


NICHE_CATALOG: dict[VerticalNiche, NicheWorkflowSpec] = {
    VerticalNiche.PRINT_PREPRESS: NicheWorkflowSpec(
        niche=VerticalNiche.PRINT_PREPRESS,
        title_ar="أتمتة مطابع الملابس (سلك سكرين) وأرشفة الأفلام وفصل الألوان",
        positioning_statement_ar="وكيل ذكي لمطابع الملابس (سلك سكرين) وفهرسة أفلام الطباعة وفصل الألوان بالعربية",
        target_segment_ar="مطابع الملابس (سلك سكرين)، مكاتب فصل الألوان، ومصانع التيشيرتات والهودي في مصر والمنطقة",
        high_value_workflows=(
            "فهرسة وأرشفة أفلام الطباعة (الكلك/البوزيتيف) والبحث الفوري فيها بالعربية لاسترجاع الموديلات وتوفير إخراج الأفلام",
            "فحص كثافة سواد الفيلم وتغطية الحبر وحساب الشبلونات والأرضية البيضاء ورقم الحرير (43T–120T) والتسعير بالجنيه",
        ),
        core_modules_used=(
            "src.screen_print.engine",
            "src.ocr.pipeline",
            "src.core.policy",
            "scripts.pii_scan",
        ),
        recommended_plan=PlanTier.TEAM,
        target_success_rate=0.90,
        managed_setup_included=True,
        payment_methods=DEFAULT_LOCAL_PAYMENTS,
    ),
    VerticalNiche.ACCOUNTING_COMPLIANCE: NicheWorkflowSpec(
        niche=VerticalNiche.ACCOUNTING_COMPLIANCE,
        title_ar="مكاتب المحاسبة والامتثال القانوني والضريبي المصري",
        positioning_statement_ar="وكيل ذكي لمكاتب المحاسبة ومراجعة الفواتير والامتثال لقانون 151/2020 بالعربية",
        target_segment_ar="مكاتب المحاسبة والمراجعة، المستشارون الماليون، وإدارات الامتثال في الشركات المصرية",
        high_value_workflows=(
            "قراءة الفواتير والإيصالات العربية بـ OCR واستخراج البنود ومطابقتها مع القيود بدليل سطر:ملف",
            "فحص العقود والمستندات لاكتشاف البيانات الشخصية المصرية (الرقم القومي/الهاتف) وضمان عدم مغادرتها محلياً",
        ),
        core_modules_used=("src.ocr.pipeline", "src.knowledge.corpus", "scripts.pii_scan", "src.awareness.calibration"),
        recommended_plan=PlanTier.BUSINESS,
        target_success_rate=0.90,
        managed_setup_included=True,
        payment_methods=DEFAULT_LOCAL_PAYMENTS,
    ),
    VerticalNiche.ARABIC_ECOMMERCE: NicheWorkflowSpec(
        niche=VerticalNiche.ARABIC_ECOMMERCE,
        title_ar="متاجر التجارة الإلكترونية العربية وإدارة الكتالوج والطلبات",
        positioning_statement_ar="وكيل ذكي للمتاجر العربية لتدقيق الكتالوج وبوالص الشحن بالعربية",
        target_segment_ar="المتاجر الإلكترونية المحلية والعلامات التجارية على سلة وزد وشوبيفاي في مصر والخليج",
        high_value_workflows=(
            "مراجعة وتوحيد أوصاف المنتجات والأسعار باللهجة المصرية/العربية الفصحى مع فحص حقن التوجيه",
            "استخراج بيانات بوالص الشحن ومرتجعات العملاء من الصور وتصنيفها تلقائياً مع حماية بيانات العميل",
        ),
        core_modules_used=("src.ocr.pipeline", "src.core.prompt_guard", "src.providers.llm_router", "src.core.pricing"),
        recommended_plan=PlanTier.PRO,
        target_success_rate=0.88,
        managed_setup_included=True,
        payment_methods=DEFAULT_LOCAL_PAYMENTS,
    ),
    VerticalNiche.DEVSECOPS_ARABIC: NicheWorkflowSpec(
        niche=VerticalNiche.DEVSECOPS_ARABIC,
        title_ar="فرق البرمجة العربية وتدقيق الأمان والامتثال (DevSecOps)",
        positioning_statement_ar="وكيل ذكي لفرق البرمجة العربية لتدقيق الكود والأمان والامتثال المحلي",
        target_segment_ar="شركات البرمجيات والـ FinTech الناشئة في مصر والمنطقة العربية",
        high_value_workflows=(
            "تشغيل حواجز الأمان الـ 7 وفحص الأسرار والبيانات الشخصية المصرية وشرح تنبيهات CodeQL بالعربية",
            "توليد تقارير امتثال EU AI Act وقانون مصر 151/2020 وSBOM موثّقة بأدلة تنفيذ فعلية",
        ),
        core_modules_used=("src.core.engine", "src.core.path_guard", "src.core.prompt_guard", "src.awareness.self_model"),
        recommended_plan=PlanTier.TEAM,
        target_success_rate=0.90,
        managed_setup_included=True,
        payment_methods=DEFAULT_LOCAL_PAYMENTS,
    ),
}


# عبارات محظورة في التموضع التسويقي (البند 5 و6: لا تقل «بديل Manus» أو «يفعل أي شيء»)
_FORBIDDEN_POSITIONING_PHRASES: tuple[tuple[str, str], ...] = (
    ("بديل manus", "لا تضع المنتج كمنافس مباشر لـ Manus؛ استخدم «وكيل لـ [مهنة محددة] بالعربية»"),
    ("manus alternative", "Avoid positioning as a generic Manus alternative; target a specific Arabic vertical"),
    ("manus killer", "Avoid generic head-on comparisons with Manus"),
    ("يفعل أي شيء", "تجنّب ادعاء «وكيل يفعل أي شيء»؛ ركّز على 1–2 مهام عالية القيمة"),
    ("يفعل كل شيء", "تجنّب ادعاء «وكيل يفعل كل شيء»؛ الادعاءات المفرطة تدمر الثقة"),
    ("does everything", "Avoid generic 'agent that does everything' positioning"),
    ("بلا حدود", "لا تدّعِ خططاً غير محدودة على تكلفة الذكاء الاصطناعي المتغيرة"),
    ("unlimited ai", "Never promise unlimited variable-cost AI inference"),
)


def get_niche_spec(niche: VerticalNiche | str) -> NicheWorkflowSpec:
    """استرجاع بطاقة النيش العمودي بالاسم أو القيمة."""
    if isinstance(niche, VerticalNiche):
        resolved = niche
    else:
        key = niche.strip().lower()
        try:
            resolved = VerticalNiche(key)
        except ValueError as exc:
            valid = ", ".join(n.value for n in VerticalNiche)
            raise ValueError(f"نيش غير معروف '{niche}'. القيم المتاحة: {valid}") from exc
    return NICHE_CATALOG[resolved]


def validate_positioning_claim(text: str) -> tuple[bool, list[str]]:
    """
    فحص الرسائل التسويقية والعروض (البند 5 و6):
    يرفض التموضع العام («بديل Manus» / «وكيل يفعل أي شيء» / «بلا حدود»)
    ويشترط ذكر المهنة أو السياق العربي المحدد.
    """
    lowered = text.lower()
    violations: list[str] = []
    for phrase, reason in _FORBIDDEN_POSITIONING_PHRASES:
        if phrase in lowered:
            violations.append(f"عبارة مخالفة للاستراتيجية ('{phrase}'): {reason}")
    return (len(violations) == 0, violations)


@dataclass(frozen=True)
class NinetyDayEvaluation:
    """
    نتيجة تقييم مقياس الـ 90 يوماً (البند 7):
      1. هل يدفع 10 مستخدمين فعلياً؟
      2. هل نسبة نجاح المهام في النيش >= 85% (ومثالية >= 90%)؟
      3. هل هامش الربح لكل مستخدم موجب؟
    """

    niche: VerticalNiche
    paying_customers: int
    task_success_rate: float
    margin_per_user_egp: float
    customers_criterion_met: bool
    reliability_criterion_met: bool
    excellent_reliability: bool
    margin_criterion_met: bool
    decision: NinetyDayDecision
    recommendation_ar: str

    @property
    def all_passed(self) -> bool:
        return (
            self.customers_criterion_met
            and self.reliability_criterion_met
            and self.margin_criterion_met
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "niche": self.niche.value,
            "paying_customers": self.paying_customers,
            "task_success_rate": round(self.task_success_rate, 4),
            "margin_per_user_egp": round(self.margin_per_user_egp, 2),
            "customers_criterion_met": self.customers_criterion_met,
            "reliability_criterion_met": self.reliability_criterion_met,
            "excellent_reliability": self.excellent_reliability,
            "margin_criterion_met": self.margin_criterion_met,
            "all_passed": self.all_passed,
            "decision": self.decision.value,
            "recommendation_ar": self.recommendation_ar,
        }


def evaluate_90_day_gate(
    niche: VerticalNiche | str,
    *,
    paying_customers: int,
    task_success_rate: float,
    margin_per_user_egp: float,
    min_paying_customers: int = MIN_PAYING_CUSTOMERS_90D,
    min_success_rate: float = MIN_NICHE_SUCCESS_RATE,
) -> NinetyDayEvaluation:
    """
    تطبيق قاعدة القرار بعد 90 يوماً (البند 7):
    - إذا تحققت الشروط الثلاثة ← `EXPAND` (وسّع النيش الحالي).
    - إذا كان الهامش سالباً أو صفراً ← `FIX_UNIT_ECONOMICS` (اضبط الحصص وModel Router).
    - إذا كانت نسبة النجاح أقل من 85% ← `FIX_WORKFLOW_RELIABILITY` (حسّن الـ Evals والـ Workflow).
    - إذا لم يصل عدد العملاء الذين يدفعون إلى 10 ← `PIVOT_NICHE_KEEP_TOOLS`
      (غيّر النيش لا الأدوات الهندسية!).
    """
    spec = get_niche_spec(niche)
    if paying_customers < 0:
        raise ValueError("paying_customers يجب أن يكون صفراً أو أكبر")
    if not (0.0 <= task_success_rate <= 1.0):
        raise ValueError("task_success_rate يجب أن يكون بين 0.0 و 1.0")

    customers_ok = paying_customers >= min_paying_customers
    reliability_ok = task_success_rate >= min_success_rate
    excellent_rel = task_success_rate >= EXCELLENT_NICHE_SUCCESS_RATE
    margin_ok = margin_per_user_egp > 0.0

    if customers_ok and reliability_ok and margin_ok:
        decision = NinetyDayDecision.EXPAND
        rec = (
            f"✅ النيش '{spec.title_ar}' اجتاز بوابة الـ 90 يوماً "
            f"({paying_customers} عملاء يدفعون، نجاح {task_success_rate * 100:.1f}%، "
            f"هامش +{margin_per_user_egp:.0f} ج.م/عميل): وسّع المبيعات وأضف الميزة التالية."
        )
    elif not margin_ok:
        decision = NinetyDayDecision.FIX_UNIT_ECONOMICS
        rec = (
            f"⚠️ هامش الربح لكل مستخدم غير موجب ({margin_per_user_egp:.0f} ج.م): "
            "فعّل توجيه المهام البسيطة لنماذج أرخص (LLMRouter) أو اضبط سقف الحصص قبل التوسع."
        )
    elif not reliability_ok:
        decision = NinetyDayDecision.FIX_WORKFLOW_RELIABILITY
        rec = (
            f"⚠️ نسبة نجاح المهام ({task_success_rate * 100:.1f}%) أقل من عتبة النيش "
            f"({min_success_rate * 100:.0f}%): ركّز على تحسين الـ 1–2 مهام الأساسية واختبارات Evals."
        )
    else:
        decision = NinetyDayDecision.PIVOT_NICHE_KEEP_TOOLS
        rec = (
            f"🔄 عدد العملاء الدافعين ({paying_customers}/{min_paying_customers}) لم يبلغ الهدف بعد 90 يوماً "
            f"رغم نجاح تقني ({task_success_rate * 100:.1f}%) وهامش موجب: "
            "غيّر النيش المستهدف واحتفظ بنفس الأدوات والبنية الهندسية!"
        )

    return NinetyDayEvaluation(
        niche=spec.niche,
        paying_customers=paying_customers,
        task_success_rate=task_success_rate,
        margin_per_user_egp=margin_per_user_egp,
        customers_criterion_met=customers_ok,
        reliability_criterion_met=reliability_ok,
        excellent_reliability=excellent_rel,
        margin_criterion_met=margin_ok,
        decision=decision,
        recommendation_ar=rec,
    )


def render_niche_playbook() -> str:
    """توليد ملخص نصي للنيشات العمودية الأربعة ومقياس الـ 90 يوماً للعرض عبر CLI."""
    lines = [
        "==========================================================================================",
        "  استراتيجية النيش العمودي لـ new-cela (البديل الواقعي لمنافسة المنصات العامة كـ Manus)",
        "==========================================================================================",
        "  القاعدة الذهبية: لا تقل «بديل Manus» — قل «وكيل ذكي لـ [مهنة محددة] بالعربية»",
        "  وسائل الدفع المحلية: InstaPay · Fawry · Paymob · Vodafone Cash (تسعير بالجنيه المصري)",
        "------------------------------------------------------------------------------------------",
    ]
    for spec in NICHE_CATALOG.values():
        lines.append(f"  ▪ [{spec.niche.value}] {spec.title_ar}")
        lines.append(f"    التموضع : {spec.positioning_statement_ar}")
        lines.append(f"    الخطة   : {spec.recommended_plan.value.upper()} | هدف الموثوقية: {spec.target_success_rate * 100:.0f}%+")
        lines.append(f"    المهمة 1: {spec.high_value_workflows[0]}")
        lines.append(f"    المهمة 2: {spec.high_value_workflows[1]}")
        lines.append("------------------------------------------------------------------------------------------")
    lines.append(
        "  مقياس القرار بعد 90 يوماً: (1) >= 10 عملاء يدفعون | (2) نجاح >= 85-90% | (3) هامش موجب"
    )
    lines.append(
        "  إذا نعم ← وسّع (EXPAND) | إذا لا ← غيّر النيش لا الأدوات (PIVOT_NICHE_KEEP_TOOLS)"
    )
    lines.append("==========================================================================================")
    return "\n".join(lines)

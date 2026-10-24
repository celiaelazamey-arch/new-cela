"""
دستور التشغيل الهندسي والتجاري (18 قاعدة) والقاعدة الذهبية
=========================================================
تُجسّد هذه الوحدة القواعد الـ 18 والقاعدة الأهم (`docs/operating-playbook-18.md`):

  «بسيط ← يعمل ← يُقاس ← يُباع ← ثم يتوسع»
  (Simple → Works → Measured → Sold → Scales)

وتوفّر 4 آليات برمجية قابلة للتنفيذ والاختبار الآلي:
  1. **بوابة القاعدة الذهبية (`evaluate_golden_rule_stage` — القاعدة الأهم + البند 1 و2 و8 و9):**
     تمنع القفز إلى التوسع (`SCALES`) أو بناء بوابة دفع آلية قبل المرور المتسلسل بـ:
     `SIMPLE` ← `WORKS` (تشغيل واختبار فعلي) ← `MEASURED` (نسبة النجاح + تكلفة المهمة)
     ← `SOLD` (3–10 عملاء يدفعون يدوياً لحل المشكلة) ← `SCALES` (>= 10 عملاء + هامش موجب).
  2. **النسخ الاحتياطي وتجربة الاستعادة الفعلية (`create_verified_backup` & `restore_and_verify_backup` — البند 6 و10 و16):**
     ينشئ نسخة احتياطية موثّقة ببصمة `SHA-256` قبل العمليات الكبيرة، ويجري اختبار
     استعادة فعلياً (`Restore Drill`) للتحقق من تطابق البايتات 100%.
  3. **بوابة الاستمرارية والـ 30 يوماً (`evaluate_30_day_activity_gate` — البند 18):**
     تفرض قاعدة «أسبوع مراجعة كل 4 أسابيع» وقاعدة «المشروع الذي لا يتقدم 30 يوماً ← أوقفه أو جمّده».
  4. **المدقق الآلي للبنود الـ 18 (`audit_18_point_playbook`):**
     يتحقق برمجياً من وجود الأدلة والملفات الحاكمة لكل بند من البنود الـ 18 في المستودع.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.core.path_guard import guard_path

# عتبات البند 2 و7 و18
MIN_MANUAL_CUSTOMERS_FOR_SOLD: int = 3
MIN_CUSTOMERS_FOR_SCALE: int = 10
MIN_SUCCESS_RATE_FOR_SCALE: float = 0.85
MAX_INACTIVE_DAYS_BEFORE_FREEZE: int = 30
REVIEW_CADENCE_WEEKS: int = 4


class ProductStage(str, Enum):
    """مراحل القاعدة الذهبية الخمس بالترتيب الإلزامي."""

    SIMPLE = "simple"        # 1. بسيط
    WORKS = "works"          # 2. يعمل
    MEASURED = "measured"    # 3. يُقاس
    SOLD = "sold"            # 4. يُباع (3–10 عملاء يدوياً)
    SCALES = "scales"        # 5. ثم يتوسع


@dataclass(frozen=True)
class GoldenRuleEvaluation:
    """نتيجة تقييم المرحلة الحالية للمنتج وفق القاعدة الذهبية."""

    current_stage: ProductStage
    next_stage: ProductStage | None
    ready_for_payment_gateway: bool
    ready_to_scale: bool
    action_required_ar: str

    def to_dict(self) -> dict[str, object]:
        return {
            "current_stage": self.current_stage.value,
            "next_stage": self.next_stage.value if self.next_stage else None,
            "ready_for_payment_gateway": self.ready_for_payment_gateway,
            "ready_to_scale": self.ready_to_scale,
            "action_required_ar": self.action_required_ar,
        }


def evaluate_golden_rule_stage(
    *,
    tests_and_ci_passing: bool,
    has_success_and_cost_metrics: bool,
    manual_paying_customers: int,
    task_success_rate: float,
    margin_per_user_egp: float,
) -> GoldenRuleEvaluation:
    """
    تقييم صارم للقاعدة الأهم: «بسيط ← يعمل ← يُقاس ← يُباع ← ثم يتوسع»
    وللبند 2: «3–10 مستخدمين يدفعون يدوياً قبل بناء بوابة الدفع».
    """
    if manual_paying_customers < 0:
        raise ValueError("manual_paying_customers يجب أن يكون صفراً أو أكبر")
    if not (0.0 <= task_success_rate <= 1.0):
        raise ValueError("task_success_rate يجب أن يكون بين 0.0 و 1.0")

    if not tests_and_ci_passing:
        return GoldenRuleEvaluation(
            current_stage=ProductStage.SIMPLE,
            next_stage=ProductStage.WORKS,
            ready_for_payment_gateway=False,
            ready_to_scale=False,
            action_required_ar=(
                "المرحلة [1/5 بسيط]: شغّل الكود واجتز جميع الاختبارات وبوابات CI أولاً "
                "(Generate ← Run ← Test ← Review ← Commit) للانتقال إلى مرحلة «يعمل»."
            ),
        )

    if not has_success_and_cost_metrics:
        return GoldenRuleEvaluation(
            current_stage=ProductStage.WORKS,
            next_stage=ProductStage.MEASURED,
            ready_for_payment_gateway=False,
            ready_to_scale=False,
            action_required_ar=(
                "المرحلة [2/5 يعمل]: النظام يعمل باختبارات خضراء — فعّل قياس أول رقمين "
                "(نسبة نجاح المهام + تكلفة المهمة) واختبارات Evals الثابتة للانتقال إلى «يُقاس»."
            ),
        )

    if manual_paying_customers < MIN_MANUAL_CUSTOMERS_FOR_SOLD:
        return GoldenRuleEvaluation(
            current_stage=ProductStage.MEASURED,
            next_stage=ProductStage.SOLD,
            ready_for_payment_gateway=False,
            ready_to_scale=False,
            action_required_ar=(
                f"المرحلة [3/5 يُقاس]: لديك ({manual_paying_customers}/{MIN_MANUAL_CUSTOMERS_FOR_SOLD}) "
                "عملاء يدفعون يدوياً — بِع المشكلة المحلولة لـ 3–10 عملاء يدوياً (InstaPay/تحويل) "
                "قبل بناء بوابة دفع آلية أو إضافة ميزات جديدة."
            ),
        )

    if (
        manual_paying_customers < MIN_CUSTOMERS_FOR_SCALE
        or task_success_rate < MIN_SUCCESS_RATE_FOR_SCALE
        or margin_per_user_egp <= 0.0
    ):
        return GoldenRuleEvaluation(
            current_stage=ProductStage.SOLD,
            next_stage=ProductStage.SCALES,
            ready_for_payment_gateway=True,
            ready_to_scale=False,
            action_required_ar=(
                f"المرحلة [4/5 يُباع]: يدفع حالياً {manual_paying_customers} عملاء يدوياً — "
                f"للتوسع إلى المرحلة 5 يجب الوصول إلى {MIN_CUSTOMERS_FOR_SCALE} عملاء يدفعون "
                f"بنسبة نجاح >= {MIN_SUCCESS_RATE_FOR_SCALE * 100:.0f}% وهامش ربح موجب لكل مستخدم."
            ),
        )

    return GoldenRuleEvaluation(
        current_stage=ProductStage.SCALES,
        next_stage=None,
        ready_for_payment_gateway=True,
        ready_to_scale=True,
        action_required_ar=(
            f"المرحلة [5/5 يتوسع]: اكتملت سلسلة «بسيط ← يعمل ← يُقاس ← يُباع ← يتوسع» "
            f"({manual_paying_customers} عملاء، نجاح {task_success_rate * 100:.1f}%، "
            f"هامش +{margin_per_user_egp:.0f} ج.م/عميل) — يمكنك الآن أتمتة بوابة الدفع والتوسع."
        ),
    )


@dataclass(frozen=True)
class BackupManifest:
    """بيان النسخة الاحتياطية الموثّقة ببصمة SHA-256 (البند 6 و16)."""

    source_name: str
    backup_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "source_name": self.source_name,
            "backup_path": self.backup_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def create_verified_backup(
    source_path: str | Path,
    backup_dir: str | Path,
    *,
    allowed_root: str | Path | None = None,
) -> BackupManifest:
    """
    إنشاء نسخة احتياطية موثّقة بـ SHA-256 قبل العمليات الكبيرة (البند 6 و16).
    """
    raw_src = Path(source_path)
    raw_dir = Path(backup_dir)
    root = Path(allowed_root).resolve() if allowed_root is not None else raw_src.resolve().parent
    safe_src = guard_path(raw_src, allowed_roots=(root,))
    safe_dir = guard_path(raw_dir, allowed_roots=(root,))

    if not safe_src.is_file():
        raise FileNotFoundError(f"ملف المصدر للنسخ الاحتياطي غير موجود: {safe_src}")

    safe_dir.mkdir(parents=True, exist_ok=True)
    backup_file = guard_path(safe_dir / f"{safe_src.name}.bak", allowed_roots=(root,))
    shutil.copy2(safe_src, backup_file)

    src_hash = _sha256_file(safe_src)
    bak_hash = _sha256_file(backup_file)
    if src_hash != bak_hash:
        raise RuntimeError("فشل التحقق من بصمة SHA-256 للنسخة الاحتياطية بعد النسخ")

    manifest = BackupManifest(
        source_name=safe_src.name,
        backup_path=str(backup_file),
        sha256=bak_hash,
        size_bytes=backup_file.stat().st_size,
    )
    manifest_file = guard_path(
        safe_dir / f"{safe_src.name}.manifest.json", allowed_roots=(root,)
    )
    manifest_file.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def restore_and_verify_backup(
    manifest: BackupManifest,
    restore_target_path: str | Path,
    *,
    allowed_root: str | Path | None = None,
) -> bool:
    """
    تنفيذ تجربة استعادة فعلية (`Restore Drill` — البند 16: «backup قابل للاستعادة — جرّب الاستعادة فعلياً»)
    والتحقق من تطابق بصمة `SHA-256` وحجم الملف بعد الاستعادة.
    """
    raw_bak = Path(manifest.backup_path)
    raw_dst = Path(restore_target_path)
    root = Path(allowed_root).resolve() if allowed_root is not None else raw_bak.resolve().parent
    safe_bak = guard_path(raw_bak, allowed_roots=(root,))
    safe_dst = guard_path(raw_dst, allowed_roots=(root,))

    if not safe_bak.is_file():
        raise FileNotFoundError(f"ملف النسخة الاحتياطية مفقود: {safe_bak}")

    safe_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(safe_bak, safe_dst)

    restored_hash = _sha256_file(safe_dst)
    restored_size = safe_dst.stat().st_size
    if restored_hash != manifest.sha256 or restored_size != manifest.size_bytes:
        raise RuntimeError(
            "فشل اختبار استعادة النسخة الاحتياطية: البصمة أو الحجم غير مطابق للأصل!"
        )
    return True


class ProjectHealthAction(str, Enum):
    """قرار الاستمرارية حسب البند 18."""

    CONTINUE_DAILY_ONE_TASK = "continue_daily_one_task"
    RUN_4_WEEK_RETROSPECTIVE = "run_4_week_retrospective"
    FREEZE_OR_STOP_PROJECT = "freeze_or_stop_project"


@dataclass(frozen=True)
class ActivityGateResult:
    """نتيجة فحص قاعدة الـ 30 يوماً وأسبوع المراجعة كل 4 أسابيع (البند 18)."""

    days_since_last_progress: int
    current_week: int
    is_review_week: bool
    should_freeze: bool
    action: ProjectHealthAction
    message_ar: str


def evaluate_30_day_activity_gate(
    days_since_last_progress: int,
    *,
    current_week: int = 1,
) -> ActivityGateResult:
    """
    البند 18 (الصحة والاستمرارية):
      - المشروع الذي لا يتقدم 30 يوماً ← أوقفه أو جمّده.
      - أسبوع مراجعة كل 4 أسابيع: ما نجح | ما نوقف.
      - مهمة رئيسية واحدة يومياً.
    """
    if days_since_last_progress < 0:
        raise ValueError("days_since_last_progress يجب ألا يكون سالباً")
    if current_week < 1:
        raise ValueError("current_week يجب أن يكون 1 أو أكبر")

    is_review = (current_week % REVIEW_CADENCE_WEEKS) == 0
    if days_since_last_progress >= MAX_INACTIVE_DAYS_BEFORE_FREEZE:
        return ActivityGateResult(
            days_since_last_progress=days_since_last_progress,
            current_week=current_week,
            is_review_week=is_review,
            should_freeze=True,
            action=ProjectHealthAction.FREEZE_OR_STOP_PROJECT,
            message_ar=(
                f"🛑 توقّف التقدم منذ {days_since_last_progress} يوماً (>= 30 يوماً): "
                "جمّد المشروع أو أوقفه فوراً وركّز على المشروع الرئيسي الوحيد."
            ),
        )

    if is_review:
        return ActivityGateResult(
            days_since_last_progress=days_since_last_progress,
            current_week=current_week,
            is_review_week=True,
            should_freeze=False,
            action=ProjectHealthAction.RUN_4_WEEK_RETROSPECTIVE,
            message_ar=(
                f"📋 الأسبوع رقم {current_week} (دورة 4 أسابيع): أسبوع مراجعة لتحديد "
                "«ما نجح فنضاعفه» و«ما لم ينجح فنوقفه»."
            ),
        )

    return ActivityGateResult(
        days_since_last_progress=days_since_last_progress,
        current_week=current_week,
        is_review_week=False,
        should_freeze=False,
        action=ProjectHealthAction.CONTINUE_DAILY_ONE_TASK,
        message_ar="✅ المشروع نشط — التزم بمهمة رئيسية واحدة يومياً مع التحقق الكامل قبل الانتقال.",
    )


# خريطة الأدلة للبنود الـ 18 في المستودع
PLAYBOOK_18_EVIDENCE: tuple[tuple[int, str, str], ...] = (
    (1, "ركّز (مشروع واحد + نيش واحد: مطبعة السلك سكرين)", "src/screen_print/engine.py"),
    (2, "عميل يدفع قبل الميزات (3–10 عملاء يدوياً)", "src/core/playbook.py"),
    (3, "لا تثق بمخرج AI قبل تشغيله (Generate←Run←Test←Review←Commit)", "CLAUDE.md"),
    (4, "CI من اليوم الأول + SHA pinning + permissions: read", ".github/workflows/quality-gate.yml"),
    (5, "الأسرار (فحص محلي pre-commit + CI + منع التسرب في السجلات)", "scripts/secret_scan.py"),
    (6, "صغّر الـ commits + backups قبل العمليات الكبيرة", "src/core/playbook.py"),
    (7, "صلاحيات أقل (قراءة فقط افتراضياً + L1 + kill switch + budget cap)", "src/core/policy.py"),
    (8, "قس قبل أن تحسّن (JSON telemetry + نسبة النجاح وتكلفة المهمة + evals)", "src/awareness/metrics.py"),
    (9, "تحكم في تكلفة AI (model router + حدود لكل خطة + حساب الهامش)", "src/core/pricing.py"),
    (10, "تفادي الاعتماد على مزود واحد (fallback Gemini/Groq + تصدير JSONL)", "src/providers/llm_router.py"),
    (11, "الخصوصية والامتثال (قانون مصر 151/2020 — 31 أكتوبر 2026 + EU AI Act)", "docs/compliance-framework.md"),
    (12, "اعتماديات وsupply chain (dependency_guard + Dependabot + pip-audit)", ".github/dependabot.yml"),
    (13, "وثّق القرارات (CLAUDE.md + AGENT_HANDOFF.md + spec_check)", "scripts/spec_check.py"),
    (14, "العربية ميزة + دفع محلي (Fawry / InstaPay / Paymob) وتسعير بالجنيه", "src/core/niche_strategy.py"),
    (15, "بنية مجانية بذكاء (concurrency + paths + تخزين JSONL محلي + allow_paid=False)", "src/core/quota.py"),
    (16, "اختبار الفشل (chaos tests + circuit breaker + backup قابل للاستعادة فعلياً)", "tests/test_chaos_awareness.py"),
    (17, "ابنِ علناً (README + CHANGELOG.md + دراسة حالة العميل #0)", "CHANGELOG.md"),
    (18, "الصحة والاستمرارية (مهمة واحدة يومياً + مراجعة 4 أسابيع + تجميد بعد 30 يوماً)", "src/awareness/self_model.py"),
)


def audit_18_point_playbook(repo_root: str | Path | None = None) -> tuple[bool, list[dict[str, object]]]:
    """فحص وجود جميع الملفات الحاكمة للبنود الـ 18 في المستودع."""
    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parent.parent.parent
    results: list[dict[str, object]] = []
    all_ok = True
    for num, title, rel_path in PLAYBOOK_18_EVIDENCE:
        exists = (root / rel_path).exists()
        if not exists:
            all_ok = False
        results.append(
            {
                "point": num,
                "title": title,
                "evidence_path": rel_path,
                "verified": exists,
            }
        )
    return (all_ok, results)

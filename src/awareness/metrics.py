"""
مقاييس الوعي الوظيفي (Awareness Metrics) — الترقية بالأرقام لا بالانطباع
=======================================================================
الدفعة سألت: «ما مقاييس النجاح؟» — والجواب يجب أن يكون قابلاً للحساب من بيانات
محلية، لا شعاراً. هنا نحسب:

  • **MTTD** — متوسط زمن الاكتشاف (وقوع العطل ← اكتشافه).
  • **MTTR** — متوسط زمن الإصلاح (اكتشاف ← حلّ).
  • نسبة الأعطال المكتشفة ذاتياً، ونسبة القرارات المعكوسة بشرياً
    (من `CalibrationLedger`).
  • معايرة الثقة (Brier/ECE) من السجل نفسه.
  • تغطية نموذج الذات (من `SelfModel.coverage()`) وعدد عناصره المتقادمة.

ثم **فحص الترقية** بين مستويات الاستقلالية (L0 → L3): كل انتقال له عتبات
رقمية صريحة؛ إن لم تتحقق كلها يبقى المستوى كما هو، والسبب مكتوب.

المصدر الوحيد للأرقام: سجلات محلية (JSONL). لا خدمات مدفوعة.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from src.awareness.calibration import CalibrationLedger, CalibrationSummary
from src.awareness.self_model import SelfModel
from src.core.policy import AutonomyLevel


class Incident(BaseModel):
    """عطل واحد بثلاثة أزمنة — ما يكفي لحساب MTTD/MTTR."""

    id: str = ""
    occurred_at: str
    detected_at: str = ""
    resolved_at: str = ""
    detected_by: str = "system"  # system | human
    diagnosis_correct: bool | None = None  # هل شخّص النظام السبب صحيحاً؟
    note: str = ""

    def _parse(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def time_to_detect(self) -> float | None:
        start, end = self._parse(self.occurred_at), self._parse(self.detected_at)
        if start is None or end is None:
            return None
        return max(0.0, (end - start).total_seconds())

    def time_to_resolve(self) -> float | None:
        start, end = self._parse(self.detected_at), self._parse(self.resolved_at)
        if start is None or end is None:
            return None
        return max(0.0, (end - start).total_seconds())


class MetricsSnapshot(BaseModel):
    """لقطة مقاييس — كل حقل إما رقم مقيس أو None (لا بيانات)، لا تقدير."""

    at: str = ""
    incidents: int = 0
    mttd_seconds: float | None = None
    mttr_seconds: float | None = None
    self_detection_rate: float | None = None
    diagnosis_accuracy: float | None = None
    reversal_rate: float | None = None
    calibration: CalibrationSummary = Field(default_factory=CalibrationSummary)
    self_model_coverage: float = 0.0
    stale_capabilities: int = 0
    drift_findings: int = 0

    def render(self) -> str:
        def secs(value: float | None) -> str:
            return "—" if value is None else f"{value:.0f}ث"

        def pct(value: float | None) -> str:
            return "—" if value is None else f"{value:.0%}"

        return "\n".join(
            [
                f"الأعطال: {self.incidents} · MTTD {secs(self.mttd_seconds)} · MTTR {secs(self.mttr_seconds)}",
                (
                    f"اكتشاف ذاتي {pct(self.self_detection_rate)} · "
                    f"تشخيص صحيح {pct(self.diagnosis_accuracy)} · "
                    f"قرارات معكوسة {pct(self.reversal_rate)}"
                ),
                (
                    f"تغطية نموذج الذات {self.self_model_coverage:.0%} · "
                    f"متقادم {self.stale_capabilities} · انجراف {self.drift_findings}"
                ),
                self.calibration.render(),
            ]
        )


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def compute_metrics(
    *,
    ledger: CalibrationLedger | None = None,
    incidents: Sequence[Incident] = (),
    self_model: SelfModel | None = None,
    root: str | None = None,
    now: datetime | None = None,
) -> MetricsSnapshot:
    """يجمع اللقطة من المصادر الثلاثة؛ أي مصدر غائب ⇒ حقوله None لا صفر مزيّف."""
    now = now or datetime.now(UTC)
    summary = ledger.summary() if ledger is not None else CalibrationSummary()

    ttd = [v for item in incidents if (v := item.time_to_detect()) is not None]
    ttr = [v for item in incidents if (v := item.time_to_resolve()) is not None]
    detected = [item for item in incidents if item.detected_at]
    diagnosed = [item for item in incidents if item.diagnosis_correct is not None]

    coverage = 0.0
    stale = 0
    drift = 0
    if self_model is not None:
        coverage = self_model.coverage(now)
        stale = len(self_model.stale(now))
        drift = len(self_model.drift(root=root, now=now))

    # نسبة الاكتشاف الذاتي: من الأعطال إن وُجدت، وإلا من سجل المعايرة.
    self_rate: float | None
    if detected:
        self_rate = round(sum(1 for item in detected if item.detected_by == "system") / len(detected), 4)
    else:
        self_rate = summary.self_detection_rate

    return MetricsSnapshot(
        at=now.isoformat(timespec="seconds"),
        incidents=len(incidents),
        mttd_seconds=_mean(ttd),
        mttr_seconds=_mean(ttr),
        self_detection_rate=self_rate,
        diagnosis_accuracy=(
            round(sum(1 for item in diagnosed if item.diagnosis_correct) / len(diagnosed), 4)
            if diagnosed
            else None
        ),
        reversal_rate=summary.reversal_rate,
        calibration=summary,
        self_model_coverage=coverage,
        stale_capabilities=stale,
        drift_findings=drift,
    )


# ================= الترقية بين المستويات =================
class PromotionCriteria(BaseModel):
    """عتبات صريحة لكل انتقال — أرقام تُراجَع وتُعدَّل علناً، لا انطباعات."""

    min_records: int
    min_accuracy: float
    max_ece: float
    max_reversal_rate: float
    min_coverage: float
    max_stale: int = 0
    max_drift: int = 0


# كلما ارتفع المستوى المستهدف اشتدّت العتبات.
PROMOTION_CRITERIA: dict[AutonomyLevel, PromotionCriteria] = {
    AutonomyLevel.L1: PromotionCriteria(
        min_records=10, min_accuracy=0.70, max_ece=0.20, max_reversal_rate=0.30, min_coverage=0.50
    ),
    AutonomyLevel.L2: PromotionCriteria(
        min_records=30, min_accuracy=0.85, max_ece=0.10, max_reversal_rate=0.10, min_coverage=0.70
    ),
    AutonomyLevel.L3: PromotionCriteria(
        min_records=100, min_accuracy=0.95, max_ece=0.05, max_reversal_rate=0.02, min_coverage=0.90
    ),
}


class PromotionVerdict(BaseModel):
    target: AutonomyLevel
    eligible: bool
    failures: list[str] = Field(default_factory=list)
    criteria: PromotionCriteria

    def render(self) -> str:
        head = f"الترقية إلى {self.target.name}: {'مؤهَّل' if self.eligible else 'غير مؤهَّل'}"
        if not self.failures:
            return head
        return head + "\n" + "\n".join(f"  ✗ {item}" for item in self.failures)


def promotion_check(snapshot: MetricsSnapshot, target: AutonomyLevel) -> PromotionVerdict:
    """
    هل تسمح الأرقام بالانتقال إلى `target`؟ كل شرط فاشل يُذكر بقيمته والعتبة.

    L0 لا يحتاج ترقية (هو الأدنى). أي حقل None يُعدّ فشلاً: «لا بيانات» ≠ «ناجح».
    """
    if target is AutonomyLevel.L0:
        return PromotionVerdict(
            target=target,
            eligible=True,
            criteria=PromotionCriteria(
                min_records=0, min_accuracy=0, max_ece=1, max_reversal_rate=1, min_coverage=0
            ),
        )
    criteria = PROMOTION_CRITERIA[target]
    cal = snapshot.calibration
    failures: list[str] = []

    if cal.records < criteria.min_records:
        failures.append(f"سجلات المعايرة {cal.records} < {criteria.min_records}")
    if cal.accuracy is None or cal.accuracy < criteria.min_accuracy:
        failures.append(f"الدقة {_fmt(cal.accuracy)} < {criteria.min_accuracy:.0%}")
    if cal.ece is None or cal.ece > criteria.max_ece:
        failures.append(f"ECE {_fmt(cal.ece, pct=False)} > {criteria.max_ece:.2f}")
    if cal.reversal_rate is None or cal.reversal_rate > criteria.max_reversal_rate:
        failures.append(f"القرارات المعكوسة {_fmt(cal.reversal_rate)} > {criteria.max_reversal_rate:.0%}")
    if snapshot.self_model_coverage < criteria.min_coverage:
        failures.append(f"تغطية نموذج الذات {snapshot.self_model_coverage:.0%} < {criteria.min_coverage:.0%}")
    if snapshot.stale_capabilities > criteria.max_stale:
        failures.append(f"عناصر متقادمة {snapshot.stale_capabilities} > {criteria.max_stale}")
    if snapshot.drift_findings > criteria.max_drift:
        failures.append(f"انجراف نموذج الذات {snapshot.drift_findings} > {criteria.max_drift}")

    return PromotionVerdict(target=target, eligible=not failures, failures=failures, criteria=criteria)


def _fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "لا بيانات"
    return f"{value:.0%}" if pct else f"{value:.3f}"

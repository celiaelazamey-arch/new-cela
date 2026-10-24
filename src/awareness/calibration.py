"""
سجل المعايرة (Calibration Ledger) — الثقة مقابل الصحة، بالأرقام
================================================================
`metacognition.estimate()` يُنتج ثقة **استدلالية** من اكتمال الأدلة. لكن السؤال
الذي لا يجيب عنه الاستدلال وحده: «عندما قلتَ 0.8 في الماضي، كم مرة كنتَ على حق؟»

هذا الملف يُغلق تلك الفجوة بثلاثة أشياء قابلة للعدّ:

  1. **سجل تنبؤ ← نتيجة** لكل مهمة (`CalibrationRecord`)، يُحفظ JSONL محلياً
     (ميزانية صفر — لا خدمة خارجية).
  2. **مقاييس معايرة** من السجل: دقة تاريخية لكل نوع مهمة، Brier score،
     خطأ المعايرة المتوقع (ECE)، نسبة القرارات التي عكسها البشر، ونسبة
     الأعطال التي اكتشفها النظام بنفسه.
  3. **بوابة قرار** (`gate`): تحت عتبة ثقة محددة ⇒ **تصعيد** أو **رفض**،
     لا تنفيذ واثق. العتبة رقم صريح، والحكم مكتوب بسببه.

قواعد صلبة:
  • «صحيح» هنا يعني نتيجة **خارجية** (اختبار نفّذه verifier مستقل، أو حكم بشري)،
    لا ادّعاء النظام عن نفسه. حقل `source` يُسجّل من حكم.
  • السجل التالف لا يُسقط النظام: الأسطر غير الصالحة تُحصى وتُبلَّغ، لا تُخفى.
  • لا رقم بلا تفسير: كل حكم من `gate` يحمل `reason`.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("new-cela.calibration")

DEFAULT_THRESHOLD = 0.5  # تحت هذه الثقة الفعلية ⇒ تصعيد
REFUSE_THRESHOLD = 0.25  # تحت هذه ⇒ رفض صريح (لا تصعيد حتى)
MIN_HISTORY = 5  # أقل عدد سجلات قبل الوثوق بالدقة التاريخية

OutcomeSource = Literal["verifier", "human", "self"]
GateDecision = Literal["proceed", "escalate", "refuse"]


class CalibrationRecord(BaseModel):
    """تنبؤ واحد مقابل نتيجته الفعلية."""

    at: str = ""
    run_id: str = ""
    task_kind: str = "general"
    predicted: float = Field(ge=0.0, le=1.0)
    correct: bool
    source: OutcomeSource = "verifier"
    reversed_by_human: bool = False  # قرار نفّذه النظام ثم عكسه إنسان
    detected_by_system: bool | None = None  # للأعطال: هل اكتشفها النظام لا المستخدم؟
    note: str = ""


class CalibrationBin(BaseModel):
    """خانة واحدة في جدول المعايرة: ثقة مُعلنة مقابل نسبة صحة فعلية."""

    low: float
    high: float
    count: int
    mean_predicted: float
    accuracy: float


class CalibrationSummary(BaseModel):
    """ملخص قابل للعرض — كل رقم فيه له تعريف أعلاه."""

    records: int = 0
    corrupted_lines: int = 0
    accuracy: float | None = None
    brier: float | None = None
    ece: float | None = None
    reversal_rate: float | None = None
    self_detection_rate: float | None = None
    bins: list[CalibrationBin] = Field(default_factory=list)
    by_kind: dict[str, float] = Field(default_factory=dict)

    def render(self) -> str:
        if not self.records:
            return "لا سجلات معايرة بعد — الثقة استدلالية فقط."

        def pct(value: float | None) -> str:
            return "—" if value is None else f"{value:.0%}"

        lines = [
            f"سجلات المعايرة: {self.records}"
            + (f" (تالف مُتخطَّى: {self.corrupted_lines})" if self.corrupted_lines else ""),
            f"الدقة التاريخية: {pct(self.accuracy)} · Brier: "
            + ("—" if self.brier is None else f"{self.brier:.3f}")
            + " · ECE: "
            + ("—" if self.ece is None else f"{self.ece:.3f}"),
            (
                f"قرارات عكسها البشر: {pct(self.reversal_rate)} · "
                f"أعطال اكتشفها النظام ذاتياً: {pct(self.self_detection_rate)}"
            ),
        ]
        for kind, acc in sorted(self.by_kind.items()):
            lines.append(f"  • {kind}: دقة {acc:.0%}")
        return "\n".join(lines)


class GateVerdict(BaseModel):
    """حكم بوابة الثقة — قابل للعرض والتدقيق."""

    decision: GateDecision
    effective_confidence: float
    reason: str
    historical_accuracy: float | None = None
    history_size: int = 0

    @property
    def may_proceed(self) -> bool:
        return self.decision == "proceed"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CalibrationLedger:
    """
    سجل تنبؤات/نتائج في الذاكرة + ملف JSONL اختياري (كتابة إلحاقية).

    لا يرفع استثناء على أخطاء القرص: يسجّل تحذيراً ويُبقي البيانات حية.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else None
        self._records: list[CalibrationRecord] = []
        self.corrupted_lines = 0
        if self.path is not None and self.path.exists():
            self._load()

    # ---------------- إدخال ----------------
    def record(
        self,
        *,
        predicted: float,
        correct: bool,
        task_kind: str = "general",
        source: OutcomeSource = "verifier",
        run_id: str = "",
        reversed_by_human: bool = False,
        detected_by_system: bool | None = None,
        note: str = "",
    ) -> CalibrationRecord:
        entry = CalibrationRecord(
            at=_utc_now(),
            run_id=run_id,
            task_kind=task_kind or "general",
            predicted=round(min(1.0, max(0.0, float(predicted))), 4),
            correct=bool(correct),
            source=source,
            reversed_by_human=reversed_by_human,
            detected_by_system=detected_by_system,
            note=note[:200],
        )
        self._records.append(entry)
        self._append(entry)
        return entry

    def records(self, task_kind: str | None = None) -> list[CalibrationRecord]:
        if task_kind is None:
            return list(self._records)
        return [item for item in self._records if item.task_kind == task_kind]

    def __len__(self) -> int:
        return len(self._records)

    # ---------------- مقاييس ----------------
    def accuracy(self, task_kind: str | None = None) -> float | None:
        items = self.records(task_kind)
        if not items:
            return None
        return round(sum(1 for item in items if item.correct) / len(items), 4)

    def summary(self, bins: int = 4) -> CalibrationSummary:
        items = self._records
        if not items:
            return CalibrationSummary(records=0, corrupted_lines=self.corrupted_lines)
        kinds = sorted({item.task_kind for item in items})
        return CalibrationSummary(
            records=len(items),
            corrupted_lines=self.corrupted_lines,
            accuracy=self.accuracy(),
            brier=brier_score(items),
            ece=expected_calibration_error(items, bins=bins),
            reversal_rate=reversal_rate(items),
            self_detection_rate=self_detection_rate(items),
            bins=calibration_bins(items, bins=bins),
            by_kind={kind: acc for kind in kinds if (acc := self.accuracy(kind)) is not None},
        )

    # ---------------- بوابة القرار ----------------
    def gate(
        self,
        predicted: float,
        task_kind: str = "general",
        threshold: float = DEFAULT_THRESHOLD,
        refuse_below: float = REFUSE_THRESHOLD,
    ) -> GateVerdict:
        return gate(
            predicted,
            history=self.records(task_kind),
            threshold=threshold,
            refuse_below=refuse_below,
        )

    # ---------------- قرص ----------------
    def _append(self, entry: CalibrationRecord) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("تعذّر كتابة سجل المعايرة (%s).", type(exc).__name__)

    def _load(self) -> None:
        assert self.path is not None
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("تعذّر قراءة سجل المعايرة (%s).", type(exc).__name__)
            return
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                self._records.append(CalibrationRecord.model_validate(json.loads(stripped)))
            except (ValueError, TypeError):  # سطر تالف: يُحصى ويُبلَّغ، لا يُسقط التحميل
                self.corrupted_lines += 1
        if self.corrupted_lines:
            logger.warning("سجل المعايرة يحتوي %d سطراً تالفاً — تُخطّيت.", self.corrupted_lines)


# ================= دوال نقية (قابلة للاختبار بلا ملفات) =================
def brier_score(records: Sequence[CalibrationRecord]) -> float | None:
    """متوسط (التنبؤ − النتيجة)² — 0 مثالي، 0.25 = عشوائي عند 0.5."""
    if not records:
        return None
    total = sum((item.predicted - (1.0 if item.correct else 0.0)) ** 2 for item in records)
    return round(total / len(records), 4)


def calibration_bins(records: Sequence[CalibrationRecord], bins: int = 4) -> list[CalibrationBin]:
    bins = max(1, int(bins))
    width = 1.0 / bins
    out: list[CalibrationBin] = []
    for index in range(bins):
        low, high = index * width, (index + 1) * width
        members = [
            item
            for item in records
            if (low <= item.predicted < high) or (index == bins - 1 and item.predicted == 1.0)
        ]
        if not members:
            continue
        out.append(
            CalibrationBin(
                low=round(low, 3),
                high=round(high, 3),
                count=len(members),
                mean_predicted=round(sum(m.predicted for m in members) / len(members), 4),
                accuracy=round(sum(1 for m in members if m.correct) / len(members), 4),
            )
        )
    return out


def expected_calibration_error(records: Sequence[CalibrationRecord], bins: int = 4) -> float | None:
    """ECE = Σ (حجم الخانة / الكل) × |دقة الخانة − متوسط ثقتها|."""
    if not records:
        return None
    total = len(records)
    error = sum(
        (item.count / total) * abs(item.accuracy - item.mean_predicted)
        for item in calibration_bins(records, bins)
    )
    return round(error, 4)


def reversal_rate(records: Sequence[CalibrationRecord]) -> float | None:
    """نسبة القرارات التي عكسها إنسان بعد تنفيذها — مؤشر استقلالية متعجّلة."""
    if not records:
        return None
    return round(sum(1 for item in records if item.reversed_by_human) / len(records), 4)


def self_detection_rate(records: Sequence[CalibrationRecord]) -> float | None:
    """من بين الأعطال المُسجَّلة، كم اكتشفها النظام قبل المستخدم؟"""
    incidents = [item for item in records if item.detected_by_system is not None]
    if not incidents:
        return None
    return round(sum(1 for item in incidents if item.detected_by_system) / len(incidents), 4)


def effective_confidence(predicted: float, history: Iterable[CalibrationRecord]) -> tuple[float, float | None, int]:
    """
    يدمج الثقة المُعلنة بالدقة التاريخية **عندما يكفي التاريخ**.

    بلا تاريخ كافٍ (< MIN_HISTORY) ⇒ نعتمد المُعلنة لكن مع خصم حذر 0.05،
    لأن ثقة بلا سجل أداء هي ادّعاء غير مُختبر.
    """
    items = list(history)
    predicted = min(1.0, max(0.0, float(predicted)))
    if len(items) < MIN_HISTORY:
        return round(max(0.0, predicted - 0.05), 4), None, len(items)
    accuracy = sum(1 for item in items if item.correct) / len(items)
    # متوسط هندسي بسيط: يعاقب التفاؤل عندما يكون السجل سيئاً ولا يكافئ بلا دليل.
    merged = (predicted * accuracy) ** 0.5
    return round(merged, 4), round(accuracy, 4), len(items)


def gate(
    predicted: float,
    history: Sequence[CalibrationRecord] = (),
    threshold: float = DEFAULT_THRESHOLD,
    refuse_below: float = REFUSE_THRESHOLD,
) -> GateVerdict:
    """
    القرار: proceed / escalate / refuse — بعتبات صريحة وسبب مكتوب.

    • effective < refuse_below ⇒ **refuse**: لا ننفّذ ولا نُصعّد؛ نعلن عجزاً.
    • effective < threshold    ⇒ **escalate**: نطلب إنساناً/verifier قبل التنفيذ.
    • غير ذلك                  ⇒ **proceed** مع ذكر حجم التاريخ الذي يدعمه.
    """
    effective, accuracy, size = effective_confidence(predicted, history)
    basis = (
        f"الدقة التاريخية {accuracy:.0%} على {size} سجلاً"
        if accuracy is not None
        else f"تاريخ غير كافٍ ({size}/{MIN_HISTORY}) — خصم حذر مُطبَّق"
    )
    if effective < refuse_below:
        decision: GateDecision = "refuse"
        reason = f"الثقة الفعلية {effective:.2f} تحت حد الرفض {refuse_below:.2f}؛ {basis}."
    elif effective < threshold:
        decision = "escalate"
        reason = f"الثقة الفعلية {effective:.2f} تحت عتبة التنفيذ {threshold:.2f}؛ {basis} ⇒ تصعيد."
    else:
        decision = "proceed"
        reason = f"الثقة الفعلية {effective:.2f} ≥ {threshold:.2f}؛ {basis}."
    return GateVerdict(
        decision=decision,
        effective_confidence=effective,
        reason=reason,
        historical_accuracy=accuracy,
        history_size=size,
    )

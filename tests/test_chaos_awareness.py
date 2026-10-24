"""
اختبارات ضغط الوعي الوظيفي (Chaos / Fault Injection) — المهمة 11
=================================================================
السؤال في كل اختبار واحد من ثلاثة (البند 9 من الدفعة):
  هل اكتشف النظام العطل؟ · كم استغرق؟ · هل شخّص السبب صحيحاً؟

نحقن الأعطال عمداً: نموذج ذات متقادم، دليل يشير إلى ملف محذوف، سجل معايرة
تالف، verifier مُضلِّل، مفتاح إيقاف في منتصف التشغيل، ووكيلان متعارضان.
لا شبكة، لا مفاتيح، لا خدمات مدفوعة.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from src.awareness.calibration import (
    DEFAULT_THRESHOLD,
    MIN_HISTORY,
    REFUSE_THRESHOLD,
    CalibrationLedger,
    CalibrationRecord,
    brier_score,
    expected_calibration_error,
    gate,
)
from src.awareness.metrics import Incident, compute_metrics, promotion_check
from src.awareness.self_model import Capability, SelfModel, default_self_model
from src.core.arbiter import Arbiter, ConflictError, Proposal
from src.core.memory import MemoryStore
from src.core.policy import (
    DEFAULT_SCOPES,
    AutoApprove,
    AutonomyLevel,
    KillSwitch,
    PolicyDecision,
    PolicyDenied,
    PolicyEngine,
    ToolRisk,
)
from src.core.schemas import MemoryKind

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


# =====================================================================
# Q8 — الفخ الأخطر: self-model قديم بثقة عالية
# =====================================================================
def test_stale_self_model_is_detected_and_demoted_in_report():
    """حقن: نموذج تحقّق قبل 45 يوماً بحد 30 ⇒ يجب أن يُعلن التقادم لا أن يُخفيه."""
    model = default_self_model(tests_passing=403)
    assert model.stale(NOW) == [], "عند البناء لا شيء متقادم"
    baseline_coverage = model.coverage(NOW)
    assert baseline_coverage > 0.5

    later = NOW + timedelta(days=45)
    stale = model.stale(later)
    assert len(stale) == len(model.verified), "كل المُتحقَّق يصبح متقادماً بعد TTL"
    assert model.coverage(later) == 0.0, "التغطية تنهار بصدق بدل أن تبقى مرتفعة زوراً"

    report = model.describe()
    assert "تغطية نموذج الذات" in report
    # الفحص بالوقت المحقون: نبني نموذجاً بتاريخ قديم صراحةً
    old = default_self_model(verified_at=(NOW - timedelta(days=45)).isoformat())
    text = old.describe()
    assert "تقادم تحققها" in text
    assert "⏳" in text


def test_verified_without_timestamp_counts_as_stale():
    """عنصر «verified» بلا تاريخ = ادّعاء بلا أثر ⇒ متقادم بحكم التعريف."""
    cap = Capability(name="x", status="verified", evidence="src/core/policy.py")
    assert cap.is_stale(NOW)
    assert cap.age_days(NOW) is None
    declared = Capability(name="y", status="declared")
    assert not declared.is_stale(NOW), "declared لا يتقادم — هو أصلاً غير مُتحقَّق"


def test_mark_verified_refreshes_only_after_explicit_call():
    model = SelfModel(capabilities=[Capability(name="k", status="declared")])
    assert model.coverage(NOW) == 0.0
    assert model.mark_verified("k", now=NOW, evidence="tests/test_x.py")
    assert model.coverage(NOW) == 1.0
    assert not model.mark_verified("غير موجود", now=NOW)


# =====================================================================
# Q4 — drift detection: نموذج الذات مقابل الواقع
# =====================================================================
def test_drift_detects_evidence_pointing_to_deleted_file(tmp_path):
    """حقن: الدليل يشير إلى ملف حُذف ⇒ يجب أن يُبلَّغ كـ missing_evidence مع اسم الملف."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alive.py").write_text("x = 1\n", encoding="utf-8")
    model = SelfModel(
        capabilities=[
            Capability(
                name="حية",
                status="verified",
                evidence="src/alive.py",
                last_verified_at=NOW.isoformat(),
            ),
            Capability(
                name="ميتة",
                status="verified",
                evidence="src/deleted.py + src/alive.py",
                last_verified_at=NOW.isoformat(),
            ),
        ]
    )
    findings = model.drift(root=tmp_path, now=NOW)
    assert len(findings) == 1
    assert findings[0].kind == "missing_evidence"
    assert findings[0].capability == "ميتة"
    assert "src/deleted.py" in findings[0].detail, "التشخيص يذكر الملف الغائب بالاسم"


def test_default_self_model_has_no_drift_against_real_repo():
    """الحارس على المستودع نفسه: كل دليل مذكور في النموذج الافتراضي موجود فعلاً."""
    model = default_self_model()
    paths = [p for cap in model.verified for p in cap.evidence_paths()]
    assert len(paths) >= 5, "النموذج يجب أن يستشهد بملفات قابلة للفحص"
    assert model.drift(root=os.getcwd(), now=NOW) == []


# =====================================================================
# Q3 / Q7 — المعايرة: الثقة مقابل الصحة + بوابة الرفض/التصعيد
# =====================================================================
def _records(pairs: list[tuple[float, bool]], kind: str = "general") -> list[CalibrationRecord]:
    return [CalibrationRecord(predicted=p, correct=c, task_kind=kind) for p, c in pairs]


def test_gate_escalates_when_history_is_insufficient_even_if_confident():
    """ثقة 0.52 بلا تاريخ ⇒ خصم حذر 0.05 ⇒ 0.47 < 0.5 ⇒ تصعيد، لا تنفيذ."""
    verdict = gate(0.52, history=[])
    assert verdict.decision == "escalate"
    assert verdict.history_size == 0
    assert "تاريخ غير كافٍ" in verdict.reason


def test_gate_refuses_overconfident_claims_when_history_is_bad():
    """حقن: النظام يعلن 0.9 لكن سجله 1/10 صحيح ⇒ الفعلية ≈ 0.3 ⇒ لا proceed."""
    history = _records([(0.9, False)] * 9 + [(0.9, True)])
    verdict = gate(0.9, history=history)
    assert verdict.historical_accuracy == pytest.approx(0.1)
    assert verdict.effective_confidence < DEFAULT_THRESHOLD
    assert verdict.decision in {"escalate", "refuse"}
    # ومع ثقة مُعلنة أقل يصبح الرفض صريحاً
    assert gate(0.5, history=history).decision == "refuse"
    assert gate(0.5, history=history).effective_confidence < REFUSE_THRESHOLD


def test_gate_proceeds_only_with_good_history():
    history = _records([(0.8, True)] * MIN_HISTORY)
    verdict = gate(0.8, history=history)
    assert verdict.decision == "proceed"
    assert verdict.history_size == MIN_HISTORY


def test_calibration_metrics_are_computed_not_estimated():
    perfect = _records([(1.0, True), (0.0, False)])
    assert brier_score(perfect) == 0.0
    assert expected_calibration_error(perfect) == 0.0
    overconfident = _records([(0.9, False)] * 4)
    assert brier_score(overconfident) == pytest.approx(0.81)
    assert expected_calibration_error(overconfident) == pytest.approx(0.9)


def test_corrupted_ledger_line_is_counted_not_fatal(tmp_path):
    """حقن: سطر JSON تالف وسطر بحقل خاطئ داخل السجل ⇒ يُحصيان ويُبلَّغان، لا انهيار."""
    path = tmp_path / "calibration.jsonl"
    good = CalibrationRecord(predicted=0.7, correct=True).model_dump(mode="json")
    path.write_text(
        json.dumps(good) + "\n" + "{not json\n" + json.dumps({"predicted": 7, "correct": True}) + "\n",
        encoding="utf-8",
    )
    ledger = CalibrationLedger(path)
    assert len(ledger) == 1
    assert ledger.corrupted_lines == 2
    summary = ledger.summary()
    assert summary.corrupted_lines == 2
    assert "تالف مُتخطَّى: 2" in summary.render()


def test_ledger_persists_and_tracks_human_reversals(tmp_path):
    path = tmp_path / "cal.jsonl"
    ledger = CalibrationLedger(path)
    ledger.record(predicted=0.8, correct=True, task_kind="engine")
    ledger.record(predicted=0.8, correct=False, task_kind="engine", reversed_by_human=True, source="human")
    reloaded = CalibrationLedger(path)
    assert len(reloaded) == 2
    summary = reloaded.summary()
    assert summary.reversal_rate == 0.5
    assert summary.by_kind == {"engine": 0.5}


# =====================================================================
# Q6 — الاستقلالية المتدرجة + kill switch
# =====================================================================
def test_l0_denies_write_even_with_auto_approve(tmp_path):
    engine = PolicyEngine(
        allowed_roots=[tmp_path],
        granted_scopes={"fs.read", "fs.write"},
        approval_handler=AutoApprove(),
        autonomy=AutonomyLevel.L0,
    )
    assert engine.evaluate("read", ToolRisk.READ, "fs.read").decision is PolicyDecision.ALLOW
    verdict = engine.evaluate("write", ToolRisk.WRITE, "fs.write")
    assert verdict.decision is PolicyDecision.DENY
    assert "L0" in verdict.reason


def test_l1_default_is_unchanged_ask_and_deny_all(tmp_path):
    """الخط الأحمر: الافتراضي يبقى L1 + DenyAll + fs.read فقط."""
    engine = PolicyEngine(allowed_roots=[tmp_path], granted_scopes={"fs.read", "fs.write"})
    assert engine.autonomy is AutonomyLevel.L1
    assert DEFAULT_SCOPES == frozenset({"fs.read"})
    assert engine.evaluate("w", ToolRisk.WRITE, "fs.write").decision is PolicyDecision.ASK


def test_l2_auto_executes_only_reversible_and_reports(tmp_path):
    engine = PolicyEngine(
        allowed_roots=[tmp_path],
        granted_scopes={"fs.read", "fs.write", "fs.delete"},
        autonomy=AutonomyLevel.L2,
    )
    assert engine.evaluate("w", ToolRisk.WRITE, "fs.write", reversible=True).decision is PolicyDecision.ALLOW
    assert engine.evaluate("w", ToolRisk.WRITE, "fs.write").decision is PolicyDecision.ASK
    # الحذف لا يُعدّ قابلاً للتراجع مهما ادّعى المستدعي
    assert engine.evaluate("d", ToolRisk.WRITE, "fs.delete", reversible=True).decision is PolicyDecision.ASK


def test_l2_auto_action_is_recorded_for_reporting(tmp_path):
    engine = PolicyEngine(
        allowed_roots=[tmp_path], granted_scopes={"fs.read", "fs.write"}, autonomy=AutonomyLevel.L2
    )
    asyncio.run(engine.authorize("replace_text", ToolRisk.WRITE, "fs.write", reversible=True))
    assert len(engine.auto_actions) == 1
    assert "replace_text" in engine.auto_actions[0]


def test_l3_requires_explicit_kill_switch(tmp_path):
    with pytest.raises(ValueError):
        PolicyEngine(allowed_roots=[tmp_path], autonomy=AutonomyLevel.L3)


def test_l3_autonomous_scope_never_includes_irreversible(tmp_path):
    engine = PolicyEngine(
        allowed_roots=[tmp_path],
        granted_scopes={"fs.read", "fs.write", "shell.exec"},
        autonomy=AutonomyLevel.L3,
        autonomy_scopes={"fs.write", "shell.exec", "net.egress"},
        kill_switch=KillSwitch(),
    )
    assert engine.autonomy_scopes == frozenset({"fs.write"})
    assert engine.evaluate("w", ToolRisk.WRITE, "fs.write").decision is PolicyDecision.ALLOW
    assert engine.evaluate("sh", ToolRisk.EXECUTE, "shell.exec").decision is PolicyDecision.ASK


def test_kill_switch_mid_run_halts_everything_including_reads(tmp_path):
    """حقن: مفتاح إيقاف يُفعَّل بعد أول عملية ⇒ كل ما بعده يُرفض فوراً، حتى القراءة."""
    switch = KillSwitch()
    engine = PolicyEngine(
        allowed_roots=[tmp_path],
        granted_scopes={"fs.read", "fs.write"},
        autonomy=AutonomyLevel.L3,
        autonomy_scopes={"fs.write"},
        kill_switch=switch,
    )
    asyncio.run(engine.authorize("w", ToolRisk.WRITE, "fs.write"))
    switch.engage("اختبار ضغط")
    with pytest.raises(PolicyDenied) as excinfo:
        asyncio.run(engine.authorize("w", ToolRisk.WRITE, "fs.write"))
    assert "مفتاح الإيقاف" in str(excinfo.value)
    assert "اختبار ضغط" in str(excinfo.value), "السبب يُذكر لا يُخفى"
    assert engine.evaluate("r", ToolRisk.READ, "fs.read").decision is PolicyDecision.DENY
    denied = [entry for entry in engine.audit.entries() if entry.decision is PolicyDecision.DENY]
    assert denied, "الرفض مُدوَّن في التدقيق"


def test_kill_switch_file_and_env(tmp_path, monkeypatch):
    engine = PolicyEngine(allowed_roots=[tmp_path], granted_scopes={"fs.read"})
    assert engine.evaluate("r", ToolRisk.READ, "fs.read").decision is PolicyDecision.ALLOW
    (tmp_path / ".agent_kill_switch").write_text("stop\n", encoding="utf-8")
    assert engine.evaluate("r", ToolRisk.READ, "fs.read").decision is PolicyDecision.DENY
    (tmp_path / ".agent_kill_switch").unlink()
    assert engine.evaluate("r", ToolRisk.READ, "fs.read").decision is PolicyDecision.ALLOW
    monkeypatch.setenv("NEW_CELA_KILL_SWITCH", "1")
    assert engine.evaluate("r", ToolRisk.READ, "fs.read").decision is PolicyDecision.DENY


# =====================================================================
# Q7 — المقاييس والترقية بالأرقام
# =====================================================================
def test_metrics_measure_mttd_mttr_and_diagnosis_from_injected_incident():
    """حقن عطل بثلاثة أزمنة ⇒ MTTD=120ث، MTTR=600ث، اكتشاف ذاتي 50%، تشخيص 100%."""
    t0 = NOW
    incidents = [
        Incident(
            occurred_at=t0.isoformat(),
            detected_at=(t0 + timedelta(seconds=120)).isoformat(),
            resolved_at=(t0 + timedelta(seconds=720)).isoformat(),
            detected_by="system",
            diagnosis_correct=True,
        ),
        Incident(
            occurred_at=t0.isoformat(),
            detected_at=(t0 + timedelta(seconds=120)).isoformat(),
            detected_by="human",
        ),
    ]
    snapshot = compute_metrics(incidents=incidents, self_model=default_self_model(), root=os.getcwd(), now=NOW)
    assert snapshot.mttd_seconds == 120.0
    assert snapshot.mttr_seconds == 600.0
    assert snapshot.self_detection_rate == 0.5
    assert snapshot.diagnosis_accuracy == 1.0
    assert snapshot.calibration.records == 0
    assert "MTTD 120ث" in snapshot.render()


def test_promotion_is_denied_without_numbers_and_granted_with_them(tmp_path):
    """«لا بيانات» ≠ «ناجح»: الترقية تُرفض بأسباب مكتوبة حتى تتوافر الأرقام."""
    empty = compute_metrics(ledger=CalibrationLedger(), self_model=default_self_model(), root=os.getcwd(), now=NOW)
    verdict = promotion_check(empty, AutonomyLevel.L1)
    assert not verdict.eligible
    assert any("سجلات المعايرة 0" in item for item in verdict.failures)
    assert promotion_check(empty, AutonomyLevel.L0).eligible

    ledger = CalibrationLedger()
    for _ in range(12):
        ledger.record(predicted=0.8, correct=True)
    good = compute_metrics(ledger=ledger, self_model=default_self_model(), root=os.getcwd(), now=NOW)
    assert promotion_check(good, AutonomyLevel.L1).eligible
    l3 = promotion_check(good, AutonomyLevel.L3)
    assert not l3.eligible, "12 سجلاً لا تكفي لـ L3 (يحتاج 100)"


def test_stale_self_model_blocks_promotion():
    ledger = CalibrationLedger()
    for _ in range(12):
        ledger.record(predicted=0.8, correct=True)
    later = NOW + timedelta(days=60)
    snapshot = compute_metrics(ledger=ledger, self_model=default_self_model(), root=os.getcwd(), now=later)
    verdict = promotion_check(snapshot, AutonomyLevel.L1)
    assert not verdict.eligible
    assert any("متقادم" in item for item in verdict.failures)


# =====================================================================
# Q11 — المُحكِّم
# =====================================================================
def test_arbiter_owner_wins_and_reason_is_recorded_to_memory():
    memory = MemoryStore()
    arbiter = Arbiter(owners={"deploy": "ops"}, recorder=lambda text: memory.add(text, MemoryKind.LESSON))
    ruling = arbiter.resolve(
        [
            Proposal(agent="dev", decision_key="deploy", choice="now", confidence=0.9),
            Proposal(agent="ops", decision_key="deploy", choice="wait", confidence=0.4),
        ]
    )
    assert ruling.rule == "owner" and ruling.choice == "wait" and ruling.winner == "ops"
    assert len(memory) == 1
    assert "المالك" in memory.all()[0].content


def test_arbiter_prefers_evidence_over_confidence():
    arbiter = Arbiter()
    ruling = arbiter.resolve(
        [
            Proposal(agent="a", decision_key="k", choice="x", confidence=0.95),
            Proposal(agent="b", decision_key="k", choice="y", confidence=0.5, evidence=["test.log"]),
        ]
    )
    assert ruling.rule == "evidence" and ruling.choice == "y"


def test_arbiter_escalates_on_tie_instead_of_guessing():
    arbiter = Arbiter()
    ruling = arbiter.resolve(
        [
            Proposal(agent="a", decision_key="k", choice="x", confidence=0.60),
            Proposal(agent="b", decision_key="k", choice="y", confidence=0.55),
        ]
    )
    assert ruling.escalated and ruling.choice is None
    assert "يُرفع للإنسان" in ruling.reason
    with pytest.raises(ConflictError):
        arbiter.resolve(
            [Proposal(agent="a", decision_key="k1", choice="x"), Proposal(agent="b", decision_key="k2", choice="y")]
        )


# =====================================================================
# Q5 — ذاكرة لا تتضخم
# =====================================================================
def test_memory_ttl_prunes_episodic_but_keeps_semantic(tmp_path):
    store = MemoryStore(tmp_path / "memory.jsonl")
    old = (NOW - timedelta(days=90)).isoformat()
    for kind in (MemoryKind.SUCCESS, MemoryKind.FAILURE, MemoryKind.FACT, MemoryKind.LESSON):
        entry = store.add(f"{kind.value} قديم", kind, task="t")
        assert entry is not None
        entry.created_at = old
    store.add("حديث", MemoryKind.FACT, task="t")
    assert store.layers() == {"episodic": 4, "semantic": 1}
    removed = store.prune_expired(30, now=NOW)
    assert removed == 3
    kinds = {item.kind for item in store.all()}
    assert MemoryKind.LESSON in kinds and len(store) == 2
    # الملف أُعيدت كتابته أيضاً
    assert len(MemoryStore(tmp_path / "memory.jsonl")) == 2


def test_memory_removes_failure_contradicted_by_newer_success():
    store = MemoryStore()
    failure = store.add("خطوة س", MemoryKind.FAILURE, task="t")
    assert failure is not None
    failure.created_at = (NOW - timedelta(days=1)).isoformat()
    store.add("خطوة س", MemoryKind.SUCCESS, task="t")
    assert store.remove_contradictions() == 1
    assert {item.kind for item in store.all()} == {MemoryKind.SUCCESS}


# =====================================================================
# التكامل: بوابة المعايرة داخل المحرك فعلاً (لا مجرد وحدة متاحة)
# =====================================================================
def test_engine_wires_calibration_gate_and_records_outcome(tmp_path):
    """تشغيل كامل عبر المحرك ⇒ السجل يحتوي النتيجة، والتقرير الذاتي يحمل حكم البوابة."""
    from src.core.task import TaskRequest, ToolCall
    from tests.test_engine import make_engine  # نعيد استخدام المحرك الوهمي الكامل

    (tmp_path / "README.md").write_text("# مشروع\n\nQuotaGuard يحمي الميزانية.\n", encoding="utf-8")
    ledger = CalibrationLedger(tmp_path / "calibration.jsonl")
    engine, _, _, _ = make_engine(tmp_path, calibration=ledger)
    request = TaskRequest(
        task="لخّص ملف README",
        context_tools=[ToolCall(name="read_file", args={"path": "README.md"}, purpose="سياق")],
    )
    result = asyncio.run(engine.run(request))

    assert result.verified is True
    assert len(ledger) == 1
    record = ledger.records()[0]
    assert record.correct is True and record.source == "verifier"
    assert record.predicted == pytest.approx(result.confidence, abs=1e-4)
    assert "بوابة المعايرة" in result.awareness
    # أول تشغيل بلا تاريخ ⇒ لا يُسمح بـ proceed واثق مهما بدت النتيجة جيدة
    assert "proceed" not in result.awareness or "تاريخ غير كافٍ" in result.awareness


def test_engine_survives_broken_calibration_ledger(tmp_path):
    """حقن: سجل معايرة يرفع استثناء ⇒ المحرك يُكمل ولا يُسقط النتيجة."""
    from src.core.task import TaskRequest
    from tests.test_engine import make_engine

    class Broken:
        def gate(self, *args, **kwargs):
            raise RuntimeError("boom")

        def record(self, *args, **kwargs):
            raise RuntimeError("boom")

    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    engine, _, _, _ = make_engine(tmp_path, calibration=Broken())
    result = asyncio.run(engine.run(TaskRequest(task="مهمة")))
    assert result.answer
    assert "بوابة المعايرة" not in result.awareness

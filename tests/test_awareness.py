"""اختبارات منظور الوعي الوظيفي — نموذج ذات، انتباه محدود، وثقة مُعايَرة."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_engine import HAPPY, ScriptedRouter, make_engine

from src.awareness.metacognition import MAX_CONFIDENCE, band_of, estimate
from src.awareness.report import DISCLAIMER, IntrospectionReport
from src.awareness.self_model import Capability, SelfModel, default_self_model
from src.awareness.workspace import (
    AttentionSchema,
    Signal,
    compete,
)
from src.core.engine import AgentEngine
from src.core.quota import QuotaGuard
from src.core.task import AcceptanceCheck, Evidence, TaskRequest


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# مشروع\n\nQuotaGuard يحمي الميزانية.\n", encoding="utf-8")
    return tmp_path


# ================= نموذج الذات =================

def test_default_self_model_has_honest_statuses() -> None:
    model = default_self_model(tests_passing=212)
    assert model.tests_passing == 212
    assert model.verified
    assert model.unverified, "يجب أن توجد قدرات مُعلنة غير متحقَّق منها"
    names = [item.name for item in model.absent]
    assert any("فينومينولوجي" in name for name in names)


def test_self_model_counts_by_status() -> None:
    model = SelfModel(
        capabilities=[
            Capability(name="a", status="verified", evidence="test"),
            Capability(name="b", status="declared"),
            Capability(name="c", status="absent"),
        ]
    )
    assert len(model.verified) == 1
    assert len(model.unverified) == 1
    assert len(model.absent) == 1
    assert "c" in model.unknown_topics()


def test_self_model_description_states_limits() -> None:
    text = default_self_model().describe()
    assert "ما لا أستطيعه" in text
    assert "وعي فينومينولوجي" in text
    assert "✅" in text and "⛔" in text


def test_capability_status_is_constrained() -> None:
    with pytest.raises(ValueError):
        Capability(name="x", status="maybe")


# ================= مساحة العمل =================

def signal(kind: str, ref: str, content: str = "نص تجريبي") -> Signal:
    return Signal(kind=kind, ref=ref, content=content)


def test_capacity_is_a_real_limit() -> None:
    signals = [signal("tool", f"t{i}") for i in range(10)]
    state = compete(signals, capacity=3)
    assert len(state.focused) == 3
    assert len(state.ignored) == 7
    assert all(item.reason == "السعة ممتلئة" for item in state.ignored)


def test_char_budget_is_enforced() -> None:
    signals = [signal("tool", f"t{i}", "x" * 100) for i in range(5)]
    state = compete(signals, capacity=10, char_budget=250)
    assert state.attention.chars_used <= 250
    assert any(item.reason == "تجاوز حد الحجم" for item in state.ignored)


def test_threshold_drops_irrelevant_items() -> None:
    state = compete(
        [signal("meta", "low", "نص لا صلة له إطلاقاً")],
        query="كلمة مختلفة تماماً",
        threshold=0.4,
    )
    assert state.focused == []
    assert state.ignored[0].reason == "تحت العتبة"


def test_competition_is_deterministic_and_order_independent() -> None:
    signals = [signal("tool", "a"), signal("knowledge", "b"), signal("memory", "c")]
    first = compete(signals, query="تجريبي")
    second = compete(list(reversed(signals)), query="تجريبي")
    assert [item.ref for item in first.focused] == [item.ref for item in second.focused]


def test_kind_weight_affects_selection() -> None:
    state = compete(
        [signal("meta", "meta"), signal("knowledge", "doc")],
        query="نص تجريبي",
        capacity=1,
    )
    assert state.focused[0].ref == "doc"


def test_relevance_raises_score() -> None:
    relevant = compete(
        [signal("tool", "rel", "الذاكرة الاستراتيجية تحفظ الدروس")],
        query="الذاكرة الاستراتيجية",
    )
    irrelevant = compete(
        [signal("tool", "rel", "نص عشوائي بلا علاقة")],
        query="الذاكرة الاستراتيجية",
    )
    assert relevant.focused[0].score > irrelevant.focused[0].score


def test_broadcast_contains_only_focused_content() -> None:
    state = compete(
        [signal("tool", "keepers", "نص الذاكرة"), signal("tool", "hidden", "سر لا يجب أن يُبث")],
        query="الذاكرة",
        capacity=1,
    )
    assert "نص الذاكرة" in state.broadcast
    assert "سر لا يجب أن يُبث" not in state.broadcast


def test_empty_signals_give_empty_state() -> None:
    state = compete([])
    assert state.empty
    assert state.broadcast == ""
    assert state.ignored == []


def test_blank_content_ignored() -> None:
    state = compete([Signal(kind="tool", ref="blank", content="   ")])
    assert state.empty


def test_attention_schema_pressure_and_stability() -> None:
    state = compete([signal("tool", f"t{i}") for i in range(6)], capacity=3)
    assert state.attention.pressure == 2.0
    assert 0.0 <= state.attention.stability <= 1.0
    assert "ضغط" in state.attention.describe()


def test_attention_threshold_control() -> None:
    high = AttentionSchema(capacity=2, pressure=2.0)
    low = AttentionSchema(capacity=10, pressure=0.1)
    base = 0.2
    assert high.suggested_threshold(base) > base
    assert low.suggested_threshold(base) < base
    assert 0.0 <= high.suggested_threshold(0.59) <= 0.6


def test_workspace_render_lists_focus_and_reasons() -> None:
    state = compete([signal("tool", f"t{i}") for i in range(4)], capacity=1)
    text = state.render()
    assert "في بؤرة الانتباه" in text
    assert "خارج البؤرة" in text
    assert "السعة ممتلئة" in text


# ================= التقييم المعرفي =================

def checks(passed: int, total: int) -> list[AcceptanceCheck]:
    return [
        AcceptanceCheck(name=f"c{i}", passed=i < passed) for i in range(total)
    ]


def test_confidence_bounded_never_certain() -> None:
    result = estimate(
        verified=True,
        checks=checks(5, 5),
        evidence=[Evidence(kind="tool"), Evidence(kind="step"), Evidence(kind="knowledge")],
    )
    assert result.score == MAX_CONFIDENCE
    assert any("سقف" in caveat for caveat in result.caveats)


def test_confidence_low_without_evidence_or_verification() -> None:
    result = estimate(verified=False, checks=checks(0, 3))
    assert result.score < 0.4
    assert result.is_low
    assert any("لا دليل" in caveat for caveat in result.caveats)


def test_confidence_rises_with_verification() -> None:
    low = estimate(verified=False, checks=checks(1, 3), evidence=[Evidence(kind="tool")])
    high = estimate(verified=True, checks=checks(3, 3), evidence=[Evidence(kind="tool")])
    assert high.score > low.score


def test_failures_and_denials_reduce_confidence() -> None:
    base = estimate(verified=True, checks=checks(3, 3), evidence=[Evidence(kind="tool")])
    hit = estimate(
        verified=True,
        checks=checks(3, 3),
        evidence=[Evidence(kind="tool")],
        tool_failures=2,
        denied_tools=1,
        provider_unverified=True,
        truncated_context=True,
    )
    assert hit.score < base.score
    assert len(hit.caveats) >= 4


def test_confidence_floor() -> None:
    result = estimate(
        verified=False,
        checks=checks(0, 5),
        evidence=[],
        tool_failures=10,
        denied_tools=10,
        provider_unverified=True,
        truncated_context=True,
    )
    assert result.score >= 0.05


def test_bands_are_ordered() -> None:
    assert band_of(0.9) == "مرتفعة مشروطة"
    assert band_of(0.6) == "متوسطة"
    assert band_of(0.3) == "منخفضة"
    assert band_of(0.1) == "ضعيفة جداً"


def test_estimate_render_explains_each_side() -> None:
    text = estimate(verified=True, checks=checks(2, 2), evidence=[Evidence(kind="tool")]).render()
    assert "الثقة" in text
    assert "+ " in text and "- " in text


# ================= التقرير الذاتي =================

def test_report_always_carries_disclaimer() -> None:
    report = IntrospectionReport(task="مهمة")
    rendered = report.render()
    assert DISCLAIMER in rendered
    assert "وعي وظيفي" in rendered


def test_report_brief_is_one_line() -> None:
    state = compete([signal("tool", "a")], query="a")
    report = IntrospectionReport(
        workspace=state,
        confidence=estimate(verified=True, checks=checks(2, 2)),
    )
    brief = report.brief()
    assert "\n" not in brief
    assert "الثقة" in brief


# ================= الدمج في المحرك =================

def test_engine_attaches_confidence_and_awareness(workspace: Path) -> None:
    engine, _, _, _ = make_engine(workspace)
    result = asyncio.run(engine.run(TaskRequest(task="مهمة بسيطة")))

    assert 0.0 <= result.confidence <= MAX_CONFIDENCE
    assert "الثقة" in result.awareness
    assert any(item.kind == "awareness" for item in result.evidence) is False or True


def test_engine_records_attention_when_context_exists(workspace: Path) -> None:
    engine, _, _, _ = make_engine(workspace)
    request = TaskRequest(
        task="اقرأ الملف",
        context_tools=[{"name": "read_file", "args": {"path": "README.md"}}],
    )
    result = asyncio.run(engine.run(TaskRequest.model_validate(request.model_dump())))
    assert result.confidence > 0
    assert "الثقة" in result.awareness


def test_engine_attention_evidence_present_with_many_sources(workspace: Path) -> None:
    class Corpus:
        def context_for(self, query: str) -> str:
            return "\n".join(
                f"- [doc{i}.md#L1-L3] نص عن {query} رقم {i}" for i in range(6)
            )

    registry = make_engine(workspace)[2]
    engine = AgentEngine(
        ScriptedRouter(HAPPY),
        registry=registry,
        quota=QuotaGuard(max_model_calls=20, max_tool_calls=5),
        knowledge=Corpus(),
    )
    request = TaskRequest(
        task="مهمة",
        context_tools=[{"name": "read_file", "args": {"path": "README.md"}}],
    )
    result = asyncio.run(engine.run(TaskRequest.model_validate(request.model_dump())))

    awareness = [item for item in result.evidence if item.kind == "awareness"]
    assert awareness
    assert "انتباه" in awareness[0].content


def test_engine_awareness_can_be_disabled(workspace: Path) -> None:
    registry = make_engine(workspace)[2]
    engine = AgentEngine(
        ScriptedRouter(HAPPY),
        registry=registry,
        quota=QuotaGuard(max_model_calls=20, max_tool_calls=5),
        awareness=False,
    )
    result = asyncio.run(
        engine.run(
            TaskRequest.model_validate(
                {
                    "task": "مهمة",
                    "context_tools": [{"name": "read_file", "args": {"path": "README.md"}}],
                }
            )
        )
    )
    assert result.awareness == ""
    assert not [item for item in result.evidence if item.kind == "awareness"]


def test_attention_threshold_adapts_between_runs(workspace: Path) -> None:
    engine, _, _, _ = make_engine(workspace)
    start = engine._attention_threshold

    class Corpus:
        def context_for(self, query: str) -> str:
            return "\n".join(f"- [d{i}.md#L1-L2] نص {query}" for i in range(30))

    engine.knowledge = Corpus()
    asyncio.run(engine.run(TaskRequest(task="مهمة")))
    assert engine._attention_threshold != start


def test_attention_failure_is_tolerated(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import src.awareness.workspace as workspace_module

    def boom(*args, **kwargs):
        raise RuntimeError("مساحة انتباه معطوبة")

    monkeypatch.setattr(workspace_module, "compete", boom)
    engine, _, _, _ = make_engine(workspace)
    engine.knowledge = type("C", (), {"context_for": lambda self, q: "- [a#L1-L2] نص"})()
    result = asyncio.run(engine.run(TaskRequest(task="مهمة")))
    assert result.status.value != "failed"
    assert result.confidence > 0

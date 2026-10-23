"""اختبارات اختيار المرشحين تحت الميزانية — ودخول القرار فعلياً في المحرك."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from test_engine import HAPPY, ScriptedRouter, make_engine

from src.core.engine import AgentEngine
from src.core.quota import QuotaGuard
from src.core.task import TaskRequest, ToolCall
from src.quantum.selector import (
    DEFAULT_TOOL_COST,
    TOOL_COST_ESTIMATE,
    build_problem,
    cost_of,
    select_under_budget,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """نفس مساحة اختبارات المحرك — كي تعمل الاختبارات بمعزل عن ترتيب الملفات."""
    (tmp_path / "README.md").write_text("# مشروع\n\nQuotaGuard يحمي الميزانية.\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def run():\n    return 'QuotaGuard'\n", encoding="utf-8")
    return tmp_path


def calls(*names: str) -> list[ToolCall]:
    return [ToolCall(name=name, args={}) for name in names]


def test_cost_lookup_and_default() -> None:
    assert cost_of("run_command") == TOOL_COST_ESTIMATE["run_command"]
    assert cost_of("tool_not_listed") == DEFAULT_TOOL_COST


def test_no_items_returns_empty_outcome() -> None:
    outcome = select_under_budget([], budget=5)
    assert outcome.chosen == []
    assert outcome.method == "none"


def test_budget_covers_everything_skips_optimization() -> None:
    items = calls("read_file", "search_files")
    outcome = select_under_budget(items, budget=10)
    assert outcome.method == "all"
    assert outcome.chosen == items
    assert "لا حاجة" in outcome.reasons[0]


def test_selection_respects_budget() -> None:
    items = calls("read_file", "search_files", "run_command")
    outcome = select_under_budget(items, budget=3)
    spent = sum(cost_of(call.name) for call in outcome.chosen)
    assert spent <= 3
    assert outcome.chosen
    assert [item for item in outcome.chosen if item in outcome.skipped] == []
    assert len(outcome.chosen) + len(outcome.skipped) == len(items)


def test_selection_never_worse_than_greedy() -> None:
    items = calls("read_file", "search_files", "run_command", "write_file")
    outcome = select_under_budget(items, budget=3)
    assert outcome.greedy is not None
    assert outcome.quantum is not None
    if outcome.method.startswith("quantum"):
        assert outcome.quantum.energy <= outcome.greedy.energy + 1e-9
        assert outcome.quantum.feasible
    else:
        assert outcome.method == "greedy"
        assert outcome.quantum.feasible is False or (
            outcome.quantum.energy > outcome.greedy.energy - 1e-9
        )


def test_selection_ignores_quantum_when_disabled() -> None:
    items = calls("read_file", "search_files", "run_command")
    outcome = select_under_budget(items, budget=2, use_quantum=False)
    assert outcome.method == "greedy"
    assert sum(cost_of(call.name) for call in outcome.chosen) <= 2


def test_outcome_render_lists_choice_and_reason() -> None:
    outcome = select_under_budget(calls("read_file", "run_command"), budget=2)
    text = outcome.render()
    assert "اختيار أدوات السياق" in text
    assert "المختار" in text
    assert "•" in text


def test_priority_order_matters() -> None:
    """الميزانية تكفي أداة واحدة: الأولى في الترتيب يجب أن تُقدَّم."""
    items = calls("read_file", "run_command", "search_files")
    outcome = select_under_budget(items, budget=1)
    assert [call.name for call in outcome.chosen] == ["read_file"]


def test_build_problem_values_decay_by_order() -> None:
    problem, lookup = build_problem(calls("a", "b", "c"), budget=2)
    assert [c.value for c in problem.candidates] == [3.0, 2.0, 1.0]
    assert lookup["a"].name == "a"


def test_duplicate_names_get_unique_keys() -> None:
    problem, lookup = build_problem(calls("read_file", "read_file"), budget=1)
    keys = [c.key for c in problem.candidates]
    assert len(set(keys)) == 2
    assert len(lookup) == 2


# ================= الدخول في المحرك =================

def test_engine_selects_tools_under_budget(workspace: Path) -> None:
    engine = AgentEngine(
        ScriptedRouter(HAPPY),
        registry=make_engine(workspace)[2],
        quota=QuotaGuard(max_model_calls=20, max_tool_calls=1),
    )
    request = TaskRequest(
        task="اجمع سياقاً",
        context_tools=[
            ToolCall(name="read_file", args={"path": "README.md"}),
            ToolCall(name="search_files", args={"query": "QuotaGuard"}),
        ],
    )
    result = asyncio.run(engine.run(request))
    selection = [item for item in result.evidence if item.kind == "selection"]
    assert selection, "القرار لم يُسجَّل كدليل"
    assert "المختار" in selection[0].content
    assert result.tool_calls <= 1


def test_engine_skips_selection_when_budget_allows(workspace: Path) -> None:
    engine, _, _, _ = make_engine(workspace)
    request = TaskRequest(
        task="اقرأ الملف",
        context_tools=[ToolCall(name="read_file", args={"path": "README.md"})],
    )
    result = asyncio.run(engine.run(request))
    assert not [item for item in result.evidence if item.kind == "selection"]
    assert result.tool_calls == 1


def test_selection_can_be_disabled(workspace: Path) -> None:
    registry = make_engine(workspace)[2]
    engine = AgentEngine(
        ScriptedRouter(HAPPY),
        registry=registry,
        quota=QuotaGuard(max_model_calls=20, max_tool_calls=50),
        optimize_selection=False,
    )
    request = TaskRequest(
        task="سياق",
        context_tools=[ToolCall(name="read_file", args={"path": "README.md"})],
    )
    result = asyncio.run(engine.run(request))
    assert not [item for item in result.evidence if item.kind == "selection"]


def test_selector_failure_keeps_all_tools(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """انهيار المحسّن يجب ألا يُفقد المهمة أدواتها."""
    import src.quantum.selector as selector_module

    def boom(*args, **kwargs):
        raise RuntimeError("محسّن معطوب")

    monkeypatch.setattr(selector_module, "select_under_budget", boom)
    engine = AgentEngine(
        ScriptedRouter(HAPPY),
        registry=make_engine(workspace)[2],
        quota=QuotaGuard(max_model_calls=20, max_tool_calls=1),
    )
    request = TaskRequest(
        task="سياق",
        context_tools=[
            ToolCall(name="read_file", args={"path": "README.md"}),
            ToolCall(name="read_file", args={"path": "src/app.py"}),
        ],
    )
    result = asyncio.run(engine.run(request))
    assert not [item for item in result.evidence if item.kind == "selection"]
    assert any(item.kind == "tool" for item in result.evidence)

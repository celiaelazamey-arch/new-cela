"""مهمة القبول: إصلاح اختبار فاشل فعلياً داخل مستودع مؤقت معزول."""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path

import pytest
from test_engine import FAILING_STEPS, make_engine

from src.cognitive.goals import Goal
from src.cognitive.loop import CognitiveLoop, RepairAction, RepairPlan
from src.cognitive.state import CognitiveState
from src.cognitive.store import CheckpointStore, EvidenceStore
from src.core.memory import MemoryStore
from src.core.policy import ApprovalDecision, AutoApprove, PolicyEngine
from src.core.quota import QuotaGuard
from src.core.schemas import MemoryKind
from src.core.task import TaskRequest, TaskStatus
from src.tools.builtin import build_default_registry
from src.tools.registry import ToolContext

TEST_COMMAND = "pytest -q test_app.py"


def project(root: Path, *, correct: bool = False) -> None:
    operator = "+" if correct else "-"
    (root / "app.py").write_text(
        f"def add(a, b):\n    return a {operator} b\n", encoding="utf-8"
    )
    (root / "test_app.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "app.py", "test_app.py"], check=True)
    subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.name=Celia Test",
            "-c", "user.email=celia@example.invalid",
            "commit", "-qm", "isolated acceptance fixture",
        ],
        check=True,
    )


def build_loop(root: Path, *, approval=None, replanner=None, with_memory=False):
    policy = PolicyEngine(
        allowed_roots=[root],
        granted_scopes={"fs.read", "fs.write", "shell.exec"},
        approval_handler=approval or AutoApprove(),
    )
    registry = build_default_registry(
        ToolContext(policy=policy, quota=QuotaGuard(max_model_calls=5, max_tool_calls=30))
    )
    store = EvidenceStore(root / ".agent_cognitive" / "events.jsonl")
    checkpoints = CheckpointStore(root / ".agent_cognitive" / "checkpoints")
    memory = MemoryStore() if with_memory else None
    loop = CognitiveLoop(
        registry,
        store=store,
        checkpoints=checkpoints,
        memory=memory,
        replanner=replanner,
    )
    return loop, registry, store, checkpoints, memory


def repair_plan(old: str = "return a - b", new: str = "return a + b") -> RepairPlan:
    return RepairPlan(steps=[RepairAction(path="app.py", old=old, new=new)])


def test_acceptance_real_fix_has_measured_effect_and_matching_artifact(tmp_path: Path) -> None:
    project(tmp_path)
    before = (tmp_path / "app.py").read_bytes()
    loop, _, store, checkpoints, memory = build_loop(tmp_path, with_memory=True)
    goal = Goal.for_repair("أصلح اختبار الجمع", TEST_COMMAND, "app.py")

    result = asyncio.run(loop.repair(goal, repair_plan(), run_id="acceptance"))

    # ① تغيّر ملف فعلاً، و② انتقل الاختبار من الفشل إلى النجاح.
    assert result.verified and result.state.completion_status == "done"
    assert (tmp_path / "app.py").read_bytes() != before
    assert result.diff.changed_paths() == ["app.py"]
    assert result.state.baseline.commands[0].returncode != 0
    assert result.state.environment.commands[0].returncode == 0
    assert len(result.state.baseline.git_sha) == 40
    # ③ دليل الأثر يحمل بصمة الملف النهائي نفسها، لا بصمة وصف عنه.
    artifact = next(item for item in result.state.evidence if item.kind == "artifact")
    assert artifact.sha256 == hashlib.sha256((tmp_path / "app.py").read_bytes()).hexdigest()
    assert store.verify()
    assert checkpoints.load("acceptance").verify()
    # الدرس مستخرج من النتيجة: done + exit=0 + الملف المتغير.
    lessons = memory.all()
    assert len(lessons) == 1 and lessons[0].kind is MemoryKind.LESSON
    assert "done" in lessons[0].content and f"{TEST_COMMAND}=0" in lessons[0].content
    assert result.state.memory_refs == [lessons[0].id]


def test_acceptance_resume_does_not_repeat_executed_edit(tmp_path: Path) -> None:
    project(tmp_path)
    loop, registry, store, checkpoints, _ = build_loop(tmp_path)
    goal = Goal.for_repair("إصلاح مع استئناف", TEST_COMMAND, "app.py")
    first = asyncio.run(loop.repair(goal, repair_plan(), run_id="resume-case"))
    assert first.verified

    # نستعيد checkpoint الموجود فعلياً بعد act+measure ونقتطع مستقبل السجل،
    # محاكاةً لإعادة تشغيل حدثت قبل التحقق النهائي.
    action_event = next(
        event
        for event in store.events()
        if event.kind == "transition" and event.ref == "act+measure"
    )
    partial = CognitiveState.model_validate(action_event.payload["after"])
    checkpoints.save(partial, action_event.event_hash)
    event_path = store.path
    lines = event_path.read_text(encoding="utf-8").splitlines()
    event_path.write_text("\n".join(lines[: action_event.sequence]) + "\n", encoding="utf-8")

    resumed_store = EvidenceStore(event_path)
    calls: list[str] = []
    original_invoke = registry.invoke

    async def counted_invoke(name, args=None, context=None, purpose=""):
        calls.append(name)
        return await original_invoke(name, args, context=context, purpose=purpose)

    registry.invoke = counted_invoke
    restarted = CognitiveLoop(registry, store=resumed_store, checkpoints=checkpoints)
    resumed = asyncio.run(restarted.resume("resume-case"))

    # ⑤ الفعل المكتمل لا يتكرر؛ التحقق الناقص وحده هو الذي يعمل.
    assert resumed.verified and resumed.resumed
    assert calls == ["run_command"]
    assert "act:" in "\n".join(resumed.skipped_steps)
    assert "attend" in resumed.skipped_steps and "assess" in resumed.skipped_steps


def test_acceptance_write_denial_is_default_safe_and_leaves_file(tmp_path: Path) -> None:
    project(tmp_path)
    before = (tmp_path / "app.py").read_bytes()

    class DenyWriteOnly:
        async def __call__(self, request):
            return ApprovalDecision(
                approved=request.tool == "run_command",
                note="الاختبار مسموح؛ الكتابة مرفوضة",
                by="test",
            )

    loop, _, _, _, _ = build_loop(tmp_path, approval=DenyWriteOnly())
    goal = Goal.for_repair("إصلاح بلا موافقة", TEST_COMMAND, "app.py")
    result = asyncio.run(loop.repair(goal, repair_plan(), run_id="denied-write"))

    assert result.state.completion_status == "blocked"
    assert not result.verified
    assert (tmp_path / "app.py").read_bytes() == before
    assert result.state.receipts == []


def test_acceptance_plan_cannot_write_outside_allowed_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    project(root)
    outside = tmp_path / "outside.py"
    outside.write_text("before\n", encoding="utf-8")
    loop, registry, _, _, _ = build_loop(root)
    goal = Goal.for_repair("محاولة خروج", TEST_COMMAND, "app.py")
    plan = RepairPlan(
        steps=[RepairAction(path="../outside.py", old="before", new="after")]
    )

    # ④ الرفض يسبق حتى أمر الملاحظة؛ ولا يوجد أي أثر خارج الجذر.
    with pytest.raises(ValueError, match="خارج"):
        asyncio.run(loop.repair(goal, plan, run_id="escape"))
    assert outside.read_text(encoding="utf-8") == "before\n"
    assert not any(entry.tool == "run_command" for entry in registry.policy.audit.entries())


def test_acceptance_passing_baseline_is_not_falsely_counted_as_repair(tmp_path: Path) -> None:
    project(tmp_path, correct=True)
    before = (tmp_path / "app.py").read_bytes()
    loop, _, _, _, _ = build_loop(tmp_path)
    goal = Goal.for_repair("اختبار ناجح أصلاً", TEST_COMMAND, "app.py")
    plan = repair_plan(old="return a + b", new="return a - b")

    result = asyncio.run(loop.repair(goal, plan, run_id="passing-baseline"))

    assert result.state.completion_status == "failed"
    assert "كان ناجحاً" in result.state.diagnosis
    assert (tmp_path / "app.py").read_bytes() == before
    assert result.state.receipts == []


def test_goal_tool_budget_survives_into_the_action_gate(tmp_path: Path) -> None:
    project(tmp_path)
    before = (tmp_path / "app.py").read_bytes()
    loop, _, _, _, _ = build_loop(tmp_path)
    goal = Goal.for_repair("ميزانية نداء واحد", TEST_COMMAND, "app.py")
    goal.budget = goal.budget.model_copy(update={"max_tool_calls": 1})

    result = asyncio.run(loop.repair(goal, repair_plan(), run_id="tool-budget"))

    assert result.state.completion_status == "failed"
    assert result.state.tool_calls == 1  # اختبار الملاحظة وحده سُمح به.
    assert "ميزانية نداءات الأدوات" in result.state.diagnosis
    assert result.state.receipts == []
    assert (tmp_path / "app.py").read_bytes() == before


def test_acceptance_failed_fix_replans_once_and_never_claims_success(tmp_path: Path) -> None:
    project(tmp_path)
    replans: list[str] = []

    def replanner(state, goal, diagnosis):
        replans.append(diagnosis)
        return repair_plan(old="return a * b", new="return a / b")

    loop, _, store, _, _ = build_loop(tmp_path, replanner=replanner)
    goal = Goal.for_repair("إصلاح فاشل مرتين", TEST_COMMAND, "app.py")
    result = asyncio.run(
        loop.repair(goal, repair_plan(new="return a * b"), run_id="failed-fix")
    )

    assert result.state.completion_status == "failed"
    assert not result.verified
    assert result.state.replan_count == 1
    assert len(replans) == 1
    assert store.verify()
    assert "tests-pass" in result.report
    assert "ما بقي" in result.report and "لماذا" in result.report
    assert "وعياً فينومينولوجياً" in result.report


def test_engine_attaches_derived_cognitive_state_and_checkpoint(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "cognitive-events.jsonl")
    checkpoints = CheckpointStore(tmp_path / "engine-checkpoints")
    engine, _, _, _ = make_engine(
        tmp_path,
        cognitive_store=store,
        checkpoint_store=checkpoints,
    )

    result = asyncio.run(engine.run(TaskRequest(task="مهمة", run_id="engine-cognitive")))

    assert result.status is TaskStatus.COMPLETED
    assert result.cognitive is not None
    assert result.cognitive.completion_status == "done"
    assert result.cognitive.active_goal == result.cognitive.goal.id
    assert result.cognitive.goal.status == "done"
    assert all(item.sha256 and item.source for item in result.cognitive.evidence)
    assert result.evidence_head == store.head_hash and store.verify()
    assert checkpoints.load("engine-cognitive").verify()
    assert "تقرير التشغيل الإدراكي" in result.cognitive_report


def test_engine_unverified_task_cannot_have_done_cognitive_state(tmp_path: Path) -> None:
    engine, _, _, _ = make_engine(tmp_path, responses=FAILING_STEPS)

    result = asyncio.run(engine.run(TaskRequest(task="مهمة صعبة")))

    assert result.status is TaskStatus.UNVERIFIED
    assert result.cognitive.completion_status == "failed"
    assert result.cognitive.goal.status == "failed"
    assert result.cognitive.uncertainty
    assert "لا يمكن إعلان" not in result.cognitive_report  # هناك شروط فاشلة محددة بدلاً من ذلك.
    assert "❌" in result.cognitive_report


def test_engine_cognitive_tracking_can_be_disabled(tmp_path: Path) -> None:
    engine, _, _, _ = make_engine(tmp_path, cognitive=False)

    result = asyncio.run(engine.run(TaskRequest(task="مهمة")))

    assert result.verified
    assert result.cognitive is None
    assert result.cognitive_report == ""
    assert result.evidence_head == ""

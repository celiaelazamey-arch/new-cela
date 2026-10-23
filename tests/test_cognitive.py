"""اختبارات عقود Celia Cognitive Core: حالة، أهداف، أدلة، عالم وأدوات."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from src.cognitive.goals import Goal, SuccessCriterion
from src.cognitive.loop import (
    RepairAction,
    RepairPlan,
    decide_next_action,
    evaluate_goal,
)
from src.cognitive.state import (
    GENESIS_HASH,
    Checkpoint,
    CognitiveState,
    apply_transition,
)
from src.cognitive.store import (
    CheckpointStore,
    EvidenceIntegrityError,
    EvidenceStore,
)
from src.cognitive.world import CommandObservation, WorldModel, WorldSnapshot
from src.core.evidence import Evidence
from src.core.policy import AutoApprove, PolicyEngine
from src.core.quota import QuotaGuard
from src.tools.builtin import build_default_registry
from src.tools.registry import ToolContext, ToolResult


def make_state(task: str = "مهمة") -> CognitiveState:
    goal = Goal.for_engine(task)
    return CognitiveState(
        task=task,
        run_id="run-test",
        active_goal=goal.id,
        goal=goal,
        verification_plan=["engine:all-required-checks"],
    )


def make_registry(root: Path, approval=None, scopes=("fs.read", "fs.write", "shell.exec")):
    policy = PolicyEngine(
        allowed_roots=[root],
        granted_scopes=set(scopes),
        approval_handler=approval or AutoApprove(),
    )
    return build_default_registry(
        ToolContext(policy=policy, quota=QuotaGuard(max_model_calls=5, max_tool_calls=30))
    )


# ================= الأهداف والحالة والقرار =================

def test_repair_goal_has_executable_criteria_and_budget() -> None:
    goal = Goal.for_repair("أصلح الجمع", "pytest -q", "app.py")
    assert goal.verification_commands == ["pytest -q"]
    assert {item.kind for item in goal.success_criteria} == {"command_exit", "file_changed"}
    assert goal.budget.max_steps == 3
    assert goal.budget.max_replans == 1
    assert goal.status == "in_progress"


def test_criterion_requires_an_executable_target() -> None:
    with pytest.raises(ValueError, match="أمراً"):
        SuccessCriterion(id="empty", kind="command_exit")
    with pytest.raises(ValueError, match="مسار"):
        SuccessCriterion(id="empty", kind="file_changed")


def test_goal_hierarchy_flattens_in_declared_order() -> None:
    child = Goal.for_engine("فرع").model_copy(
        update={"id": "child", "parent_id": "parent"}
    )
    parent = Goal.for_engine("أصل").model_copy(
        update={"id": "parent", "subgoals": [child]}
    )
    assert [item.id for item in parent.flatten()] == ["parent", "child"]
    assert parent.with_status("done").subgoals[0].status == "done"


def test_transition_has_before_after_and_valid_hashes() -> None:
    before = make_state()
    after, transition = apply_transition(
        before, stage="decide", updates={"next_action": {"kind": "verify"}}
    )
    assert after.revision == 1
    assert transition.before_hash == before.fingerprint()
    assert transition.after_hash == after.fingerprint()
    assert transition.before["revision"] == 0
    assert transition.after["next_action"] == {"kind": "verify"}
    assert transition.verify()


def test_same_state_and_update_replay_same_decision_and_hash() -> None:
    state = make_state()
    plan = RepairPlan(steps=[RepairAction(path="a.py", old="x", new="y")])
    first = decide_next_action(state, plan)
    second = decide_next_action(state.model_copy(deep=True), plan)
    assert first == second
    _, left = apply_transition(state, stage="decide", updates={"next_action": first})
    _, right = apply_transition(state, stage="decide", updates={"next_action": second})
    assert left.after_hash == right.after_hash


def test_transition_tampering_is_detected() -> None:
    state = make_state()
    _, transition = apply_transition(state, stage="assess", updates={"confidence": 0.4})
    transition.after["confidence"] = 0.9
    assert not transition.verify()


def test_checkpoint_seal_detects_content_change() -> None:
    checkpoint = Checkpoint(state=make_state()).sealed()
    assert checkpoint.verify()
    checkpoint.state.task = "مهمة مغيّرة"
    assert not checkpoint.verify()


def test_action_id_is_stable_and_noop_is_rejected() -> None:
    action = RepairAction(path="a.py", old="x", new="y")
    assert action.action_id == RepairAction(path="a.py", old="x", new="y").action_id
    assert action.step_id.startswith("act:edit-")
    with pytest.raises(ValueError, match="بلا أثر"):
        RepairAction(path="a.py", old="x", new="x")


def test_plan_rejects_duplicates_and_more_than_three_steps() -> None:
    action = RepairAction(path="a.py", old="x", new="y")
    with pytest.raises(ValueError, match="تكرر"):
        RepairPlan(steps=[action, action])
    with pytest.raises(ValueError):
        RepairPlan(
            steps=[
                RepairAction(path=f"{index}.py", old="x", new="y")
                for index in range(4)
            ]
        )


def test_decision_skips_completed_actions_then_requests_verification() -> None:
    action = RepairAction(path="a.py", old="x", new="y")
    plan = RepairPlan(steps=[action])
    state = make_state().model_copy(update={"completed_steps": [action.step_id]})
    assert decide_next_action(state, plan)["action_id"] == "verify:final"
    done = state.model_copy(update={"completion_status": "done"})
    assert decide_next_action(done, plan) is None


def test_goal_evaluation_requires_real_observations() -> None:
    goal = Goal.for_repair("إصلاح", "pytest -q", "a.py")
    missing = evaluate_goal(goal, None, None)
    assert not any(item.passed for item in missing)
    snapshot = WorldSnapshot(
        root="/tmp/root",
        commands=[CommandObservation(command="pytest -q", returncode=0)],
    )
    measured = evaluate_goal(goal, snapshot, None)
    assert next(item for item in measured if item.criterion_id == "tests-pass").passed
    assert not next(item for item in measured if item.criterion_id == "file-changed").passed


# ================= سجل الأدلة ونقاط الحفظ =================

def test_event_store_chains_and_seals_evidence(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "events.jsonl")
    assert store.head_hash == GENESIS_HASH
    event = store.append_evidence(Evidence(kind="tool", ref="read", content="نص"))
    assert event.sequence == 1
    assert event.previous_hash == GENESIS_HASH
    assert event.payload["source"] == "read"
    assert event.payload["sha256"] == hashlib.sha256("نص".encode()).hexdigest()
    second = store.append("measure", {"exit": 0})
    assert second.previous_hash == event.event_hash
    assert store.verify()


def test_event_store_persists_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = EvidenceStore(path)
    store.append("one", {"value": 1})
    store.append("two", {"value": 2})
    loaded = EvidenceStore(path)
    assert loaded.head_hash == store.head_hash
    assert [event.kind for event in loaded.events()] == ["one", "two"]


def test_event_store_detects_in_memory_payload_tamper() -> None:
    store = EvidenceStore()
    event = store.append("evidence", {"content": "original"})
    store._events[0] = event.model_copy(update={"payload": {"content": "changed"}})
    assert not store.verify()
    with pytest.raises(EvidenceIntegrityError):
        store.require_valid()


def test_event_store_rejects_tampered_file(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = EvidenceStore(path)
    store.append("evidence", {"content": "original"})
    event = json.loads(path.read_text(encoding="utf-8"))
    event["payload"]["content"] = "changed"
    path.write_text(json.dumps(event), encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore(path)


def test_store_refuses_invalid_transition() -> None:
    state = make_state()
    _, transition = apply_transition(state, stage="decide", updates={"diagnosis": "x"})
    invalid = transition.model_copy(update={"after_hash": "f" * 64})
    with pytest.raises(EvidenceIntegrityError):
        EvidenceStore().append_transition(invalid)


def test_checkpoint_roundtrip(tmp_path: Path) -> None:
    checkpoints = CheckpointStore(tmp_path / "checkpoints")
    state = make_state()
    saved = checkpoints.save(state, "a" * 64)
    loaded = checkpoints.load(state.run_id)
    assert loaded is not None
    assert loaded.checkpoint_hash == saved.checkpoint_hash
    assert loaded.state.fingerprint() == state.fingerprint()
    assert checkpoints.load("missing") is None


def test_checkpoint_tamper_is_rejected(tmp_path: Path) -> None:
    checkpoints = CheckpointStore(tmp_path)
    state = make_state()
    checkpoints.save(state, "a" * 64)
    path = tmp_path / f"{state.run_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["state"]["task"] = "تجميل السجل"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError):
        checkpoints.load(state.run_id)


def test_checkpoint_rejects_path_traversal(tmp_path: Path) -> None:
    checkpoints = CheckpointStore(tmp_path)
    with pytest.raises(ValueError, match="run_id"):
        checkpoints.load("../secret")


# ================= نموذج العالم =================

def test_world_snapshot_hashes_bytes_and_reads_git_head(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\x00\xff")
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text("abc123\n")
    snapshot = WorldModel(tmp_path).capture(["data.bin"])
    assert snapshot.git_sha == "abc123"
    assert snapshot.files["data.bin"] == hashlib.sha256(b"\x00\xff").hexdigest()
    assert snapshot.fingerprint() == WorldModel(tmp_path).capture(["data.bin"]).fingerprint()


def test_world_capture_omits_missing_and_blocks_escape(tmp_path: Path) -> None:
    world = WorldModel(tmp_path)
    assert world.capture(["missing.py"]).files == {}
    with pytest.raises(ValueError, match="خارج"):
        world.capture(["../secret"])


def test_world_diff_classifies_all_file_changes() -> None:
    before = WorldSnapshot(root="/r", files={"same": "1", "changed": "2", "removed": "3"})
    after = WorldSnapshot(root="/r", files={"same": "1", "changed": "4", "added": "5"})
    diff = WorldModel("/tmp").diff(before, after)
    assert diff.added == ["added"]
    assert diff.removed == ["removed"]
    assert diff.unchanged == ["same"]
    assert diff.changed["changed"].before == "2"
    assert diff.changed["changed"].after == "4"
    assert diff.has_file_changes


def test_world_returns_latest_command_observation() -> None:
    first = CommandObservation(command="pytest", returncode=1)
    second = CommandObservation(command="pytest", returncode=0)
    snapshot = WorldSnapshot(root="/r", commands=[first, second])
    assert snapshot.command("pytest") == second
    assert snapshot.command("other") is None


def test_command_observation_requires_a_real_exit_code() -> None:
    with pytest.raises(ValueError, match="رمز خروج"):
        CommandObservation.from_tool("pytest", ToolResult(name="run_command", ok=False))
    result = ToolResult(
        name="run_command",
        ok=False,
        returncode=2,
        stdout="collected",
        stderr="failed",
    )
    observation = CommandObservation.from_tool("pytest", result)
    assert observation.returncode == 2
    assert not observation.succeeded
    assert "exit=2" in observation.render()


# ================= أدوات الأثر الفعلي =================

def test_replace_text_writes_atomically_and_returns_hashes(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    registry = make_registry(tmp_path)
    result = asyncio.run(
        registry.invoke("replace_text", {"path": "app.py", "old": "1", "new": "2"})
    )
    assert result.ok, result.error
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    expected = hashlib.sha256(b"value = 2\n").hexdigest()
    assert f"after_sha256={expected}" in result.output


def test_replace_text_rejects_occurrence_mismatch_without_writing(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("x x\n", encoding="utf-8")
    registry = make_registry(tmp_path)
    result = asyncio.run(
        registry.invoke("replace_text", {"path": "app.py", "old": "x", "new": "y"})
    )
    assert not result.ok
    assert "عدد مواضع" in result.error
    assert target.read_text(encoding="utf-8") == "x x\n"


def test_replace_text_rejects_stale_expected_hash(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    registry = make_registry(tmp_path)
    result = asyncio.run(
        registry.invoke(
            "replace_text",
            {"path": "app.py", "old": "before", "new": "after", "expected_sha256": "0" * 64},
        )
    )
    assert not result.ok
    assert "تغيّر الملف" in result.error
    assert target.read_text(encoding="utf-8") == "before\n"


def test_replace_text_default_approval_is_denial(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("before\n", encoding="utf-8")
    # السياسة الافتراضية وحدها تُبني صراحةً مع DenyAll.
    policy = PolicyEngine(allowed_roots=[tmp_path], granted_scopes={"fs.read", "fs.write"})
    registry = build_default_registry(ToolContext(policy=policy, quota=QuotaGuard()))
    result = asyncio.run(
        registry.invoke("replace_text", {"path": "app.py", "old": "before", "new": "after"})
    )
    assert result.denied
    assert target.read_text(encoding="utf-8") == "before\n"


def test_replace_text_outside_root_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("before\n", encoding="utf-8")
    registry = make_registry(root)
    result = asyncio.run(
        registry.invoke(
            "replace_text", {"path": "../outside.py", "old": "before", "new": "after"}
        )
    )
    assert result.denied
    assert outside.read_text(encoding="utf-8") == "before\n"


def test_run_command_returns_nonzero_as_structured_measurement(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    result = asyncio.run(
        registry.invoke(
            "run_command",
            {"command": "python -c 'import sys; sys.exit(7)'", "cwd": "."},
        )
    )
    assert not result.ok
    assert not result.denied
    assert result.returncode == 7
    assert "رمز الخروج 7" in result.error


def test_run_command_cwd_cannot_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    registry = make_registry(root)
    result = asyncio.run(
        registry.invoke("run_command", {"command": "python --version", "cwd": ".."})
    )
    assert result.denied
    assert result.returncode is None

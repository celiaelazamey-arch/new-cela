"""دورة إدراك–فعل–تعلّم محدودة لإصلاح اختبار داخل جذر مسموح.

الدورة لا تشخّص الشيفرة بنفسها ولا تدّعي ذلك: تقبل خطة استبدال منظّمة (من
مستخدم أو مخطّط قابل للاختبار)، ثم تملك القرار التشغيلي والقياس والاستئناف.
النجاح يُشتق من بصمات الملفات ورموز خروج الأوامر فقط.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.awareness.metacognition import estimate
from src.awareness.self_model import SelfModel, default_self_model
from src.awareness.workspace import Signal, compete
from src.cognitive._utils import digest_json
from src.cognitive.goals import CriterionResult, Goal
from src.cognitive.report import render_cognitive_report
from src.cognitive.state import (
    ActionReceipt,
    CognitiveState,
    Transition,
    apply_transition,
)
from src.cognitive.store import CheckpointStore, EvidenceIntegrityError, EvidenceStore
from src.cognitive.world import CommandObservation, WorldDiff, WorldModel, WorldSnapshot
from src.core.evidence import Evidence
from src.core.memory import MemoryStore
from src.core.policy import PolicyDecision
from src.core.schemas import MemoryKind
from src.tools.registry import ToolRegistry, ToolResult

Replanner = Callable[[CognitiveState, Goal, str], "RepairPlan"]


class RepairAction(BaseModel):
    """فعل وحيد قابل للتنفيذ عبر `replace_text`، لا شيفرة حرة."""

    path: str = Field(..., min_length=1)
    old: str = Field(..., min_length=1)
    new: str = ""
    expected_occurrences: int = Field(default=1, ge=1, le=100)
    expected_sha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def has_effect(self):
        if self.old == self.new:
            raise ValueError("الفعل بلا أثر: old وnew متطابقان.")
        return self

    @property
    def action_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"expected_sha256"})
        return "edit-" + digest_json(payload)[:20]

    @property
    def step_id(self) -> str:
        return f"act:{self.action_id}"

    def as_decision(self) -> dict:
        return {
            "kind": "replace_text",
            "action_id": self.action_id,
            **self.model_dump(mode="json"),
        }

    def tool_args(self, current_hash: str) -> dict:
        return {
            "path": self.path,
            "old": self.old,
            "new": self.new,
            "expected_occurrences": self.expected_occurrences,
            "expected_sha256": self.expected_sha256 or current_hash,
        }


class RepairPlan(BaseModel):
    """خطة قبول محدودة: خطوة إلى ثلاث، ولا أفعال مجهولة."""

    steps: list[RepairAction] = Field(..., min_length=1, max_length=3)
    diagnosis: str = ""

    @model_validator(mode="after")
    def unique_actions(self):
        identifiers = [action.action_id for action in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("الخطة تكرر الفعل نفسه؛ اجعل كل خطوة ذات أثر مستقل.")
        return self


class CognitiveResult(BaseModel):
    """نتيجة الدورة، ومعيار `verified` مشتق لا حقل يملؤه النموذج."""

    state: CognitiveState
    goal: Goal
    criteria: list[CriterionResult] = Field(default_factory=list)
    diff: WorldDiff | None = None
    event_head: str
    checkpoint_id: str = ""
    report: str = ""
    resumed: bool = False
    skipped_steps: list[str] = Field(default_factory=list)

    @property
    def verified(self) -> bool:
        required = [result for result in self.criteria if result.required]
        return (
            self.state.completion_status == "done"
            and bool(required)
            and all(result.passed for result in required)
        )


def decide_next_action(state: CognitiveState, plan: RepairPlan) -> dict | None:
    """قرار حتمي: أول فعل غير مكتمل، ثم التحقق النهائي، ثم تقييم الدليل المحفوظ.

    الحالة التي اكتمل فيها `verify:final` وما زالت `in_progress` هي checkpoint
    استُؤنف بعد القياس النهائي وقبل الحسم؛ قرارها تقييم معايير الهدف من الدليل
    المحفوظ نفسه، لا إعادة اختبار ولا تعديل. هذا القرار لا يطلب أداة.
    """
    if state.completion_status != "in_progress":
        return None
    for action in plan.steps:
        if not state.has_completed(action.step_id):
            return action.as_decision()
    if not state.has_completed("verify:final"):
        return {"kind": "verify", "action_id": "verify:final"}
    return {"kind": "evaluate", "action_id": "evaluate:goal"}


def evaluate_goal(
    goal: Goal,
    snapshot: WorldSnapshot | None,
    diff: WorldDiff | None,
    *,
    engine_verified: bool = False,
) -> list[CriterionResult]:
    """يقيس كل معيار من ملاحظة مباشرة؛ غياب الملاحظة يعني عدم الاجتياز."""
    results: list[CriterionResult] = []
    for criterion in goal.success_criteria:
        passed = False
        detail = "لا ملاحظة متاحة"
        if criterion.kind == "command_exit":
            observation = snapshot.command(criterion.command) if snapshot else None
            if observation is not None:
                passed = observation.returncode == criterion.expected_exit_code
                detail = (
                    f"رمز الخروج {observation.returncode}؛ "
                    f"المطلوب {criterion.expected_exit_code}"
                )
        elif criterion.kind == "file_changed":
            if diff is not None:
                changed = criterion.path in diff.changed or criterion.path in diff.added
                passed = changed
                detail = "تغيّرت البصمة" if changed else "لم تتغير البصمة"
        elif criterion.kind == "engine_checks":
            passed = engine_verified
            detail = "شروط المحرك مجتازة" if passed else "شروط المحرك غير مجتازة"
        results.append(
            CriterionResult(
                criterion_id=criterion.id,
                passed=passed,
                required=criterion.required,
                detail=detail,
            )
        )
    for subgoal in goal.subgoals:
        for result in evaluate_goal(
            subgoal,
            snapshot,
            diff,
            engine_verified=engine_verified,
        ):
            results.append(
                result.model_copy(update={"criterion_id": f"{subgoal.id}:{result.criterion_id}"})
            )
    return results


class CognitiveLoop:
    """منفّذ محدود للدورة، مع بوابة أدوات وسجل ونقاط استئناف اختيارية."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        store: EvidenceStore | None = None,
        checkpoints: CheckpointStore | None = None,
        memory: MemoryStore | None = None,
        self_model: SelfModel | None = None,
        replanner: Replanner | None = None,
    ) -> None:
        self.registry = registry
        self.store = store or EvidenceStore()
        self.checkpoints = checkpoints
        self.memory = memory
        self.self_model = self_model or default_self_model()
        self.replanner = replanner
        self.world = WorldModel(registry.policy.allowed_roots[0])

    async def repair(
        self,
        goal: Goal,
        plan: RepairPlan,
        *,
        run_id: str = "",
    ) -> CognitiveResult:
        """يبدأ تشغيلاً جديداً؛ استخدم `resume` لاستئناف checkpoint موجود."""
        identifier = run_id or "repair-" + uuid.uuid4().hex[:16]
        if self.checkpoints is not None and self.checkpoints.load(identifier) is not None:
            raise ValueError(f"يوجد checkpoint بهذا run_id: {identifier}")
        self._validate_plan(goal, plan)
        state = CognitiveState(
            task=goal.description,
            run_id=identifier,
            active_goal=goal.id,
            goal=goal,
            capabilities=[capability.name for capability in self.self_model.verified],
            action_plan=[step.model_dump(mode="json") for step in plan.steps],
            verification_plan=goal.verification_commands,
        )
        self.store.append_transition(Transition.between(None, state, stage="init"))
        self._save_checkpoint(state)
        return await self._execute(state, plan, resumed=False)

    async def resume(self, run_id: str) -> CognitiveResult:
        """يستأنف الحالة المحفوظة ويتخطى كل step_id المكتمل فيها.

        إن كان `verify:final` مكتملاً والحالة `in_progress`، تُحسم الحالة من
        اللقطة المحفوظة إلى `done` أو `failed` دون إعادة اختبار أو تعديل.
        """
        if self.checkpoints is None:
            raise ValueError("الاستئناف يحتاج CheckpointStore.")
        checkpoint = self.checkpoints.load(run_id)
        if checkpoint is None:
            raise FileNotFoundError(f"لا يوجد checkpoint للتشغيل: {run_id}")
        self.store.require_valid()
        known_hashes = {event.event_hash for event in self.store.events()}
        if checkpoint.event_head not in known_hashes:
            raise EvidenceIntegrityError("رأس checkpoint غير موجود في سلسلة الأدلة الحالية.")
        state = checkpoint.state
        if state.goal is None:
            raise EvidenceIntegrityError("checkpoint بلا هدف؛ لا يمكن استئناف قراره.")
        plan = RepairPlan.model_validate({"steps": state.action_plan})
        skipped = list(state.completed_steps)
        if state.completion_status != "in_progress":
            return self._result(state, resumed=True, skipped_steps=skipped)
        return await self._execute(state, plan, resumed=True, skipped_steps=skipped)

    async def _execute(
        self,
        state: CognitiveState,
        plan: RepairPlan,
        *,
        resumed: bool,
        skipped_steps: list[str] | None = None,
    ) -> CognitiveResult:
        assert state.goal is not None
        goal = state.goal
        skipped = skipped_steps or []
        paths = self._paths(goal, plan)

        # observe: لقطة أولية قبل أي كتابة.
        if state.baseline is None:
            snapshot = self.world.capture(paths)
            state = self._commit(
                state,
                "observe",
                {"environment": snapshot, "baseline": snapshot},
                reason="لقطة ملفات وGit قبل الفعل",
            )

        # observe + gate + act: تشغيل الاختبار الفاشل وقياسه قبل التعديل.
        if not state.has_completed("observe:verify"):
            state = self._commit(
                state,
                "gate",
                {
                    "approval_required": bool(goal.verification_commands),
                    "next_action": {"kind": "verify", "action_id": "observe:verify"},
                },
                reason="أوامر الملاحظة تمر بالسياسة والموافقة",
            )
            observations, fatal, calls = await self._run_commands(
                state, goal.verification_commands
            )
            if fatal is not None:
                state = self._fatal(
                    state,
                    fatal,
                    phase="observe",
                    calls=calls,
                    observations=observations,
                )
                return self._finish(state)
            snapshot = self.world.capture(paths, observations)
            state = self._commit(
                state,
                "measure",
                {
                    "environment": snapshot,
                    "baseline": snapshot,
                    "approval_required": False,
                    "next_action": None,
                    "completed_steps": [*state.completed_steps, "observe:verify"],
                    "tool_calls": state.tool_calls + calls,
                },
                evidence=[self._command_evidence(item) for item in observations],
                reason="رمز خروج الاختبار قبل الإصلاح",
            )
        else:
            skipped.append("observe:verify")

        baseline_commands = state.baseline.commands if state.baseline else []
        if baseline_commands and self._commands_meet_goal(goal, baseline_commands):
            state = self._commit(
                state,
                "assess",
                {
                    "completion_status": "failed",
                    "goal": goal.with_status("failed"),
                    "diagnosis": "أمر الاختبار كان ناجحاً قبل الإصلاح؛ لا يوجد فشل يُنسب إلى الفعل.",
                },
                reason="رفض مهمة قبول لا تبدأ باختبار فاشل",
            )
            return self._finish(state)

        # attend: لا يُبث إلا ما فاز بالسعة، والمُهمَل يبقى مسجلاً.
        attention = self._attention(state)
        attention_uncertainty = [
            f"سياق مُهمَل ({item.ref}): {item.reason}" for item in attention.ignored
        ]
        if not state.has_completed("attend"):
            state = self._commit(
                state,
                "attend",
                {
                    "uncertainty": attention_uncertainty,
                    "completed_steps": [*state.completed_steps, "attend"],
                },
                evidence=[
                    Evidence(
                        kind="awareness",
                        ref="attention",
                        content=attention.render(),
                        source="src.awareness.workspace",
                    )
                ],
                reason="تسجيل ما دخل بؤرة الانتباه وما لم يدخل",
            )
        else:
            skipped.append("attend")

        baseline_criteria = evaluate_goal(
            goal,
            state.baseline,
            self.world.diff(state.baseline, state.baseline) if state.baseline else None,
        )
        if not state.has_completed("assess"):
            initial_confidence = estimate(
                verified=False,
                checks=baseline_criteria,
                evidence=state.evidence,
                truncated_context=bool(attention.ignored),
            )
            state = self._commit(
                state,
                "assess",
                {
                    "confidence": initial_confidence.score,
                    "uncertainty": [
                        *attention_uncertainty,
                        "الإصلاح لم يُثبت باختبار لاحق بعد",
                    ],
                    "completed_steps": [*state.completed_steps, "assess"],
                },
                reason="الثقة مشتقة من ملاحظات لا من وصف ذاتي",
            )
        else:
            skipped.append("assess")

        # decide → gate → act → measure، ثم إعادة تخطيط واحدة كحد أقصى.
        while state.completion_status == "in_progress":
            decision = decide_next_action(state, plan)
            if decision is None:
                break
            if decision["kind"] == "evaluate":
                # checkpoint استُؤنف بعد verify:final: الحسم من الدليل المحفوظ
                # وحده، دون إعادة اختبار أو تعديل أو أي نداء أداة.
                state = self._conclude(state, goal)
                break
            if decision["kind"] == "verify":
                state = await self._verify_final(state, goal, paths)
                if state.completion_status != "in_progress":
                    break
                criteria = self._measured_criteria(state, goal)
                if self._passed(criteria):
                    state = self._complete(state, criteria)
                    break

                diagnosis = self._diagnosis(criteria)
                if self.replanner is not None and state.replan_count < goal.budget.max_replans:
                    try:
                        new_plan = self.replanner(state, goal, diagnosis)
                        self._validate_plan(goal, new_plan)
                    except Exception as exc:  # noqa: BLE001 — فشل المخطط نتيجة موثقة
                        state = self._commit(
                            state,
                            "replan",
                            {
                                "completion_status": "failed",
                                "goal": goal.with_status("failed"),
                                "diagnosis": f"تعذّر إعادة التخطيط: {type(exc).__name__}",
                            },
                        )
                        break
                    completed = [step for step in state.completed_steps if step != "verify:final"]
                    state = self._commit(
                        state,
                        "replan",
                        {
                            "action_plan": [step.model_dump(mode="json") for step in new_plan.steps],
                            "completed_steps": completed,
                            "replan_count": state.replan_count + 1,
                            "diagnosis": diagnosis,
                            "next_action": None,
                        },
                        reason="تشخيص واحد وإعادة تخطيط واحدة كحد أقصى",
                    )
                    plan = new_plan
                    paths = self._paths(goal, plan)
                    continue

                state = self._commit(
                    state,
                    "assess",
                    {
                        "completion_status": "failed",
                        "goal": goal.with_status("failed"),
                        "diagnosis": diagnosis,
                        "next_action": None,
                        "approval_required": False,
                    },
                    reason="لم تُجتَز معايير الهدف بعد القياس",
                )
                break

            action = next(
                item for item in plan.steps if item.action_id == decision["action_id"]
            )
            if len(state.receipts) >= goal.budget.max_steps:
                state = self._commit(
                    state,
                    "decide",
                    {
                        "completion_status": "failed",
                        "goal": goal.with_status("failed"),
                        "diagnosis": f"استُنفدت ميزانية الخطوات ({goal.budget.max_steps}).",
                    },
                )
                break
            state = await self._apply_action(state, goal, action, paths)
            if state.completion_status != "in_progress":
                break

        return self._finish(state, resumed=resumed, skipped_steps=skipped)

    async def _apply_action(
        self,
        state: CognitiveState,
        goal: Goal,
        action: RepairAction,
        paths: list[str],
    ) -> CognitiveState:
        state = self._commit(
            state,
            "decide",
            {"next_action": action.as_decision(), "approval_required": True},
            reason="اختيار أول فعل غير مكتمل بصورة حتمية",
        )
        spec = self.registry.get("replace_text")
        if spec is None:
            return self._commit(
                state,
                "gate",
                {
                    "completion_status": "failed",
                    "goal": goal.with_status("failed"),
                    "diagnosis": "أداة replace_text غير مسجلة.",
                },
            )
        verdict = self.registry.policy.evaluate(spec.name, spec.risk, spec.scope)
        if verdict.decision is PolicyDecision.DENY:
            return self._commit(
                state,
                "gate",
                {
                    "completion_status": "blocked",
                    "goal": goal.with_status("blocked"),
                    "diagnosis": verdict.reason,
                    "approval_required": False,
                    "next_action": None,
                },
                reason="الرفض الافتراضي قبل أي كتابة",
            )

        if state.tool_calls >= goal.budget.max_tool_calls:
            return self._commit(
                state,
                "gate",
                {
                    "completion_status": "failed",
                    "goal": goal.with_status("failed"),
                    "diagnosis": f"استُنفدت ميزانية نداءات الأدوات ({goal.budget.max_tool_calls}).",
                    "approval_required": False,
                    "next_action": None,
                },
                reason="ميزانية الهدف تسبق أي أثر جديد",
            )

        before = self.world.capture(paths)
        result = await self.registry.invoke(
            "replace_text",
            action.tool_args(before.files.get(action.path, "")),
            purpose=f"هدف {goal.id}: فعل {action.action_id}",
        )
        if result.denied:
            return self._commit(
                state,
                "gate",
                {
                    "completion_status": "blocked",
                    "goal": goal.with_status("blocked"),
                    "diagnosis": result.error,
                    "approval_required": False,
                    "next_action": None,
                    "tool_calls": state.tool_calls + 1,
                },
                evidence=[self._tool_evidence("replace_text", result)],
                reason="لم تُمنح موافقة الكتابة",
            )
        if not result.ok:
            return self._commit(
                state,
                "act",
                {
                    "completion_status": "failed",
                    "goal": goal.with_status("failed"),
                    "diagnosis": result.error,
                    "approval_required": False,
                    "next_action": None,
                    "tool_calls": state.tool_calls + 1,
                },
                evidence=[self._tool_evidence("replace_text", result)],
                reason="فشل الأداة قبل إثبات أثر صالح",
            )

        after = self.world.capture(paths)
        action_diff = self.world.diff(before, after)
        changed = action.path in action_diff.changed or action.path in action_diff.added
        if not changed:
            return self._commit(
                state,
                "measure",
                {
                    "completion_status": "failed",
                    "goal": goal.with_status("failed"),
                    "diagnosis": "أبلغت الأداة نجاحاً لكن بصمة الملف لم تتغير.",
                    "approval_required": False,
                    "next_action": None,
                    "tool_calls": state.tool_calls + 1,
                },
                evidence=[self._tool_evidence("replace_text", result)],
                reason="لا تُقبل خطوة بلا أثر قابل للقياس",
            )

        before_hash = before.files.get(action.path, "")
        after_hash = after.files.get(action.path, "")
        artifact = Evidence(
            kind="artifact",
            ref=action.action_id,
            content=(
                f"path={action.path}\nbefore_sha256={before_hash}\n"
                f"after_sha256={after_hash}\n{result.output}"
            ),
            source=str(self.world.resolve(action.path)),
            sha256=after_hash,
        )
        receipt = ActionReceipt(
            action_id=action.action_id,
            kind="replace_text",
            before_hash=before_hash,
            after_hash=after_hash,
            evidence_refs=[artifact.ref],
        )
        return self._commit(
            state,
            "act+measure",
            {
                "environment": after,
                "completed_steps": [*state.completed_steps, action.step_id],
                "receipts": [*state.receipts, receipt],
                "approval_required": False,
                "next_action": None,
                "tool_calls": state.tool_calls + 1,
            },
            evidence=[self._tool_evidence("replace_text", result), artifact],
            reason="تغيّر الملف وسُجّلت بصمتا قبل/بعد",
        )

    async def _verify_final(
        self,
        state: CognitiveState,
        goal: Goal,
        paths: list[str],
    ) -> CognitiveState:
        state = self._commit(
            state,
            "decide+gate",
            {
                "next_action": {"kind": "verify", "action_id": "verify:final"},
                "approval_required": bool(goal.verification_commands),
            },
            reason="التحقق النهائي أمر قابل للتنفيذ لا نية",
        )
        observations, fatal, calls = await self._run_commands(
            state, goal.verification_commands
        )
        if fatal is not None:
            return self._fatal(
                state,
                fatal,
                phase="verify",
                calls=calls,
                observations=observations,
            )
        snapshot = self.world.capture(paths, observations)
        return self._commit(
            state,
            "measure",
            {
                "environment": snapshot,
                "completed_steps": [*state.completed_steps, "verify:final"],
                "approval_required": False,
                "next_action": None,
                "tool_calls": state.tool_calls + calls,
            },
            evidence=[self._command_evidence(item) for item in observations],
            reason="رمز خروج الاختبار بعد الأثر",
        )

    async def _run_commands(
        self,
        state: CognitiveState,
        commands: list[str],
    ) -> tuple[list[CommandObservation], ToolResult | None, int]:
        observations: list[CommandObservation] = []
        calls = 0
        goal = state.goal
        assert goal is not None
        for command in commands:
            if state.tool_calls + calls >= goal.budget.max_tool_calls:
                return observations, ToolResult(
                    name="run_command",
                    ok=False,
                    error=f"استُنفدت ميزانية نداءات الأدوات ({goal.budget.max_tool_calls}).",
                    reason="goal_budget",
                ), calls
            result = await self.registry.invoke(
                "run_command",
                {"command": command, "cwd": ".", "timeout": 120},
                purpose="معيار نجاح قابل للتنفيذ",
            )
            calls += 1
            if result.denied or result.returncode is None:
                return observations, result, calls
            observations.append(CommandObservation.from_tool(command, result))
        return observations, None, calls

    def _measured_criteria(
        self,
        state: CognitiveState,
        goal: Goal,
    ) -> list[CriterionResult]:
        """يقيس معايير الهدف من آخر لقطة محفوظة؛ قياس خالص بلا تنفيذ أمر."""
        return evaluate_goal(goal, state.environment, self._current_diff(state))

    @staticmethod
    def _diagnosis(criteria: list[CriterionResult]) -> str:
        return "؛ ".join(
            f"{result.criterion_id}: {result.detail}"
            for result in criteria
            if result.required and not result.passed
        )

    def _conclude(self, state: CognitiveState, goal: Goal) -> CognitiveState:
        """يحسم حالة اكتمل فيها التحقق النهائي: done أو failed وفق الدليل.

        تُستخدم عند الاستئناف بعد `verify:final` حيث الحالة بقيت `in_progress`.
        لا تُعيد الاختبار ولا تعدّل ملفاً ولا تسأل المخطط: القراءة من اللقطة
        المحفوظة هي المصدر الوحيد للحكم، وغياب الملاحظة يعني عدم الاجتياز.
        """
        criteria = self._measured_criteria(state, goal)
        if self._passed(criteria):
            return self._complete(state, criteria)
        return self._commit(
            state,
            "assess",
            {
                "completion_status": "failed",
                "goal": goal.with_status("failed"),
                "diagnosis": self._diagnosis(criteria),
                "next_action": None,
                "approval_required": False,
            },
            reason="حسم من دليل تحقق محفوظ؛ لا إعادة اختبار ولا تعديل",
        )

    def _complete(
        self,
        state: CognitiveState,
        criteria: list[CriterionResult],
    ) -> CognitiveState:
        assert state.goal is not None
        confidence = estimate(
            verified=True,
            checks=criteria,
            evidence=state.evidence,
        )
        return self._commit(
            state,
            "assess",
            {
                "completion_status": "done",
                "goal": state.goal.with_status("done"),
                "confidence": confidence.score,
                "diagnosis": "",
                "uncertainty": [],
                "next_action": None,
                "approval_required": False,
            },
            reason="كل معيار إلزامي اجتاز قياسه الفعلي",
        )

    def _fatal(
        self,
        state: CognitiveState,
        result: ToolResult,
        phase: str,
        *,
        calls: int = 1,
        observations: list[CommandObservation] | None = None,
    ) -> CognitiveState:
        status = "blocked" if result.denied else "failed"
        assert state.goal is not None
        return self._commit(
            state,
            "gate" if result.denied else "measure",
            {
                "completion_status": status,
                "goal": state.goal.with_status(status),
                "diagnosis": result.error or result.reason or "تعذّر قياس الأمر",
                "approval_required": False,
                "next_action": None,
                "tool_calls": state.tool_calls + calls,
            },
            evidence=[
                *(self._command_evidence(item) for item in observations or []),
                self._tool_evidence(f"{phase}:run_command", result),
            ],
            reason="تعذّر إنتاج ملاحظة أمر صالحة",
        )

    def _finish(
        self,
        state: CognitiveState,
        *,
        resumed: bool = False,
        skipped_steps: list[str] | None = None,
    ) -> CognitiveResult:
        # learn: الدرس من النتيجة المقيسة، وليس من وصف النموذج لنفسه.
        if self.memory is not None and not state.memory_refs:
            criteria = evaluate_goal(state.goal, state.environment, self._current_diff(state)) if state.goal else []
            codes = [
                f"{item.command}={item.returncode}"
                for item in (state.environment.commands if state.environment else [])
            ]
            changed = self._current_diff(state).changed_paths() if self._current_diff(state) else []
            content = (
                f"هدف «{state.task}»: {state.completion_status}. "
                f"الأوامر: {', '.join(codes) or 'لا شيء'}. "
                f"الملفات المتغيرة: {', '.join(changed) or 'لا شيء'}. "
                f"غير المتحقق: {', '.join(r.criterion_id for r in criteria if not r.passed) or 'لا شيء'}."
            )
            entry = self.memory.add(
                content=content,
                kind=MemoryKind.LESSON,
                task=state.task,
                tags=["cognitive-repair", f"status={state.completion_status}"],
            )
            if entry is not None:
                state = self._commit(
                    state,
                    "learn",
                    {"memory_refs": [entry.id]},
                    reason="درس مستخرج من رموز الخروج والبصمات",
                )
        return self._result(state, resumed=resumed, skipped_steps=skipped_steps or [])

    def _result(
        self,
        state: CognitiveState,
        *,
        resumed: bool,
        skipped_steps: list[str],
    ) -> CognitiveResult:
        goal = state.goal
        assert goal is not None
        diff = self._current_diff(state)
        criteria = evaluate_goal(goal, state.environment, diff)
        report = render_cognitive_report(
            state,
            goal=goal,
            criteria=criteria,
            diff=diff,
            event_head=self.store.head_hash,
        )
        return CognitiveResult(
            state=state,
            goal=goal,
            criteria=criteria,
            diff=diff,
            event_head=self.store.head_hash,
            checkpoint_id=f"{state.run_id}:{state.revision}",
            report=report,
            resumed=resumed,
            skipped_steps=skipped_steps,
        )

    def _commit(
        self,
        state: CognitiveState,
        stage: str,
        updates: dict[str, Any],
        *,
        evidence: list[Evidence] = (),
        reason: str = "",
    ) -> CognitiveState:
        sealed = [item.sealed() for item in evidence]
        for item in sealed:
            self.store.append_evidence(item)
        if sealed:
            updates = {**updates, "evidence": [*state.evidence, *sealed]}
        updated, transition = apply_transition(
            state,
            stage=stage,
            updates=updates,
            reason=reason,
        )
        self.store.append_transition(transition)
        self._save_checkpoint(updated)
        return updated

    def _save_checkpoint(self, state: CognitiveState) -> None:
        if self.checkpoints is not None:
            self.checkpoints.save(state, self.store.head_hash)

    def _attention(self, state: CognitiveState):
        observations = state.baseline.commands if state.baseline else []
        signals = [
            Signal(kind="tool", ref=item.command, content=item.render())
            for item in observations
        ]
        return compete(signals, query=state.task)

    def _current_diff(self, state: CognitiveState) -> WorldDiff | None:
        if state.baseline is None or state.environment is None:
            return None
        return self.world.diff(state.baseline, state.environment)

    def _paths(self, goal: Goal, plan: RepairPlan) -> list[str]:
        return sorted(
            {
                *(action.path for action in plan.steps),
                *(
                    criterion.path
                    for item in goal.flatten()
                    for criterion in item.success_criteria
                    if criterion.kind == "file_changed"
                ),
            }
        )

    def _validate_plan(self, goal: Goal, plan: RepairPlan) -> None:
        if len(plan.steps) > min(3, goal.budget.max_steps):
            raise ValueError("خطة الإصلاح تتجاوز ثلاث خطوات أو ميزانية الهدف.")
        if not goal.verification_commands:
            raise ValueError("هدف الإصلاح يحتاج أمر تحقق واحداً على الأقل.")
        for action in plan.steps:
            self.world.resolve(action.path)
        for item in goal.flatten():
            for criterion in item.success_criteria:
                if criterion.kind == "file_changed":
                    self.world.resolve(criterion.path)

    @staticmethod
    def _passed(criteria: list[CriterionResult]) -> bool:
        required = [result for result in criteria if result.required]
        return bool(required) and all(result.passed for result in required)

    @staticmethod
    def _commands_meet_goal(
        goal: Goal,
        observations: list[CommandObservation],
    ) -> bool:
        criteria = [
            criterion
            for item in goal.flatten()
            for criterion in item.success_criteria
            if criterion.kind == "command_exit"
        ]
        by_command = {item.command: item for item in observations}
        return bool(criteria) and all(
            criterion.command in by_command
            and by_command[criterion.command].returncode == criterion.expected_exit_code
            for criterion in criteria
        )

    @staticmethod
    def _command_evidence(observation: CommandObservation) -> Evidence:
        # ok تعني صلاحية الملاحظة، لا نجاح الأمر؛ الرمز داخل الدليل هو الحكم.
        return Evidence(
            kind="command",
            ref=observation.command,
            content=observation.render(),
            ok=True,
            source="run_command",
        )

    @staticmethod
    def _tool_evidence(ref: str, result: ToolResult) -> Evidence:
        return Evidence(
            kind="tool",
            ref=ref,
            content=result.as_evidence(),
            ok=result.ok,
            source=result.name,
        )


__all__ = [
    "CognitiveLoop",
    "CognitiveResult",
    "RepairAction",
    "RepairPlan",
    "decide_next_action",
    "evaluate_goal",
]

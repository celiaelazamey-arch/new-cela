"""
اختبارات عقود P0
================
أربع طبقات:
  1. الأدوات (`tools.registry` + `tools.builtin`) — تحقق وسائط، حصر مسارات، بحث، تنفيذ.
  2. السياسة والموافقات (`core.policy`) — الرفض الافتراضي، النطاقات، التدقيق.
  3. الميزانية والموجه (`core.quota` + `core.model_api`) — لا مزود مدفوع/غير متحقق.
  4. المحرك (`core.engine`) — دورة كاملة من الطلب إلى نتيجة **متحقَّقة** أو صادقة الرفض.

كل شيء بلا شبكة ولا مفاتيح، والكتابة في `tmp_path` فقط.
"""
import asyncio
import json
from pathlib import Path

import pytest

from src.core.engine import UNVERIFIED_NOTE, AgentEngine
from src.core.model_api import (
    Capability,
    CapabilityRouter,
    ModelInfo,
    ModelRouter,
    NoEligibleModel,
)
from src.core.policy import (
    AuditLog,
    AutoApprove,
    PolicyDenied,
    PolicyEngine,
    ToolRisk,
)
from src.core.quota import ProviderNotAllowed, QuotaExceeded, QuotaGuard, TrustLevel
from src.core.task import TaskRequest, TaskStatus, ToolCall
from src.tools.builtin import build_default_registry
from src.tools.registry import ToolContext, ToolRegistry, ToolSpec

# ================= أدوات مساعدة =================

class ScriptedRouter:
    """موجّه وهمي — يفي بعقد المحرك وبعقد LLMRouter معاً."""

    def __init__(self, text_responses=None, fail_at: int | None = None):
        from src.config import Settings

        self.text_responses = list(text_responses or [])
        self.fail_at = fail_at
        self.calls = 0
        self.prompts: list[str] = []
        self.settings = Settings(_env_file=None)

    def available(self):
        return [ModelInfo(provider="fake", model="fake-1", trust=TrustLevel.VERIFIED_FREE)]

    async def execute_reasoning_step(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise RuntimeError("فشل المزود الوهمي")
        return self.text_responses.pop(0) if self.text_responses else "FINAL ANSWER: رد"

    async def execute_structured_step(self, prompt: str, output_format):
        return None


HAPPY = [
    '{"steps": ["اقرأ الملف", "لخّص النتائج"]}',
    "قرأت الملف: 3 أقسام",
    '{"passed": true, "score": 9, "feedback": "جيد"}',
    "الملخص النهائي",
    '{"passed": true, "score": 8, "feedback": "مقبول"}',
    "FINAL ANSWER: ملخص موثّق",
]

FAILING_STEPS = [
    '{"steps": ["خطوة صعبة"]}',
    "محاولة",
    '{"passed": false, "score": 2, "feedback": "ناقص", "suggestion": "حسّن"}',
    "محاولة",
    '{"passed": false, "score": 2, "feedback": "ناقص", "suggestion": "حسّن"}',
    "FINAL ANSWER: نتيجة غير مكتملة",
]


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text(
        "# مشروع\n\nهذا مشروع تجريبي.\nQuotaGuard يحمي الميزانية.\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "def run():\n    return 'QuotaGuard'\n", encoding="utf-8"
    )
    return tmp_path


def make_engine(workspace: Path, responses=None, approval=None, scopes=("fs.read",), **kwargs):
    """يبني محركاً كامل التبعيات على مساحة عمل معزولة."""
    policy = PolicyEngine(
        allowed_roots=[workspace],
        granted_scopes=set(scopes),
        approval_handler=approval,
        audit=AuditLog(),
    )
    quota = kw_pop(kwargs, "quota", QuotaGuard(max_model_calls=50, max_tool_calls=20))
    registry = build_default_registry(ToolContext(policy=policy, quota=quota))
    engine = AgentEngine(
        ScriptedRouter(responses or HAPPY),
        registry=registry,
        quota=quota,
        **kwargs,
    )
    return engine, policy, registry, quota


def kw_pop(kwargs: dict, key: str, default):
    return kwargs.pop(key, default)


# ================= 1) الأدوات =================

def test_read_file_inside_root(workspace: Path):
    _, _, registry, _ = make_engine(workspace)
    result = asyncio.run(registry.invoke("read_file", {"path": "README.md"}))

    assert result.ok, result.error
    assert "QuotaGuard" in result.output
    assert result.duration_ms >= 0


def test_read_file_blocks_escape_and_absolute_paths(workspace: Path, tmp_path: Path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("سري", encoding="utf-8")
    _, _, registry, _ = make_engine(workspace)

    for path in ("../secret.txt", str(outside), "/etc/passwd"):
        result = asyncio.run(registry.invoke("read_file", {"path": path}))
        assert not result.ok, path
        assert result.denied, f"المسار {path} لم يُمنع"


def test_read_file_rejects_binary_and_reports_missing(workspace: Path):
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02")
    _, _, registry, _ = make_engine(workspace)

    binary = asyncio.run(registry.invoke("read_file", {"path": "blob.bin"}))
    assert not binary.ok and "ثنائي" in binary.error

    missing = asyncio.run(registry.invoke("read_file", {"path": "ghost.md"}))
    assert not missing.ok and "غير موجود" in missing.error


def test_search_files_finds_line_numbers(workspace: Path):
    _, _, registry, _ = make_engine(workspace)
    result = asyncio.run(registry.invoke("search_files", {"query": "QuotaGuard"}))

    assert result.ok, result.error
    assert "README.md:4" in result.output
    assert "src/app.py:2" in result.output

    empty = asyncio.run(registry.invoke("search_files", {"query": "لا-يوجد-هذا"}))
    assert empty.ok and "لا نتائج" in empty.output


def test_run_command_requires_approval(workspace: Path):
    _, _, registry, _ = make_engine(workspace)  # بلا موافقة: DenyAll
    denied = asyncio.run(registry.invoke("run_command", {"command": "echo hi"}))
    assert not denied.ok and denied.denied

    _, _, registry2, _ = make_engine(
        workspace, approval=AutoApprove(), scopes=("fs.read", "shell.exec")
    )
    approved = asyncio.run(registry2.invoke("run_command", {"command": "echo hi"}))
    assert approved.ok, approved.error
    assert "hi" in approved.output


def test_registry_validates_params_and_unknown_tools(workspace: Path):
    _, _, registry, _ = make_engine(workspace)

    unknown = asyncio.run(registry.invoke("nope", {}))
    assert not unknown.ok and "غير معروفة" in unknown.error

    invalid = asyncio.run(registry.invoke("read_file", {}))
    assert not invalid.ok and "وسائط غير صالحة" in invalid.error

    assert "read_file" in registry.names()
    assert any(item["name"] == "search_files" for item in registry.describe())


class EchoParams:
    """نموذج وسائط فارغ لأداة اختبارية."""

    @staticmethod
    def model_validate(args):
        return args

    @staticmethod
    def model_dump(**_kwargs):
        return {}


async def _noop(params, ctx):  # pragma: no cover - لا يُنفَّذ هنا
    return "ok"


def test_registry_rejects_duplicates_and_bad_names():
    registry = ToolRegistry()
    spec = ToolSpec(name="noop", description="لا شيء", params=EchoParams, handler=_noop)
    assert registry.register(spec) is spec

    with pytest.raises(ValueError, match="مكرّرة"):
        registry.register(ToolSpec(name="noop", description="", params=EchoParams, handler=_noop))
    with pytest.raises(ValueError, match="غير صالح"):
        registry.register(
            ToolSpec(name="bad name", description="", params=EchoParams, handler=_noop)
        )


def test_tool_quota_is_enforced(workspace: Path):
    quota = QuotaGuard(max_tool_calls=1)
    _, _, registry, _ = make_engine(workspace, quota=quota)

    first = asyncio.run(registry.invoke("read_file", {"path": "README.md"}))
    second = asyncio.run(registry.invoke("read_file", {"path": "README.md"}))

    assert first.ok
    assert not second.ok and "تجاوز حد نداءات الأدوات" in second.error


# ================= 2) السياسة والموافقات =================

def test_policy_denies_ungranted_scope():
    policy = PolicyEngine(allowed_roots=[Path.cwd()], granted_scopes={"fs.read"})
    verdict = policy.evaluate("run_command", ToolRisk.EXECUTE, scope="shell.exec")
    assert not verdict.allowed
    assert verdict.decision.value == "deny"

    denied_risk = PolicyEngine(granted_scopes={"shell.exec"}).evaluate(
        "run_command", ToolRisk.EXECUTE, scope="shell.exec"
    )
    assert denied_risk.decision.value == "ask"


def test_policy_default_is_denial_for_sensitive_ops():
    policy = PolicyEngine(granted_scopes={"shell.exec", "fs.write"})
    with pytest.raises(PolicyDenied, match="الموافقة"):
        asyncio.run(policy.authorize("run_command", ToolRisk.EXECUTE, scope="shell.exec"))


def test_policy_autoapprove_and_audit_redaction():
    audit = AuditLog()
    policy = PolicyEngine(
        granted_scopes={"shell.exec"},
        approval_handler=AutoApprove(),
        audit=audit,
    )
    verdict = asyncio.run(
        policy.authorize(
            "run_command",
            ToolRisk.EXECUTE,
            scope="shell.exec",
            args={"command": "echo hi", "token": "AIzaSyD-1234567890abcdefghijklmnop"},
        )
    )
    assert verdict.allowed
    entries = audit.entries()
    assert entries and entries[-1].decision.value == "allow"
    assert "AIzaSyD" not in json.dumps([e.model_dump(mode="json") for e in entries], ensure_ascii=False)


def test_policy_path_confinement():
    root = Path.cwd()
    policy = PolicyEngine(allowed_roots=[root])
    assert policy.resolve_path("README.md") == (root / "README.md").resolve()
    with pytest.raises(PolicyDenied):
        policy.resolve_path("/etc/passwd")
    with pytest.raises(PolicyDenied):
        policy.resolve_path("../outside.txt")


def test_policy_approval_budget_limit():
    policy = PolicyEngine(
        granted_scopes={"shell.exec"},
        approval_handler=AutoApprove(),
        max_approvals=1,
    )
    assert asyncio.run(policy.authorize("a", ToolRisk.EXECUTE, "shell.exec")).allowed
    with pytest.raises(PolicyDenied, match="حد الموافقات"):
        asyncio.run(policy.authorize("b", ToolRisk.EXECUTE, "shell.exec"))


# ================= 3) الميزانية والموجه =================

def test_quota_blocks_paid_and_unverified_providers():
    guard = QuotaGuard()
    guard.check_provider("gemini", TrustLevel.VERIFIED_FREE)

    with pytest.raises(ProviderNotAllowed, match="مدفوع"):
        guard.check_provider("openai", TrustLevel.PAID)
    with pytest.raises(ProviderNotAllowed, match="غير متحقَّق"):
        guard.check_provider("newprov", TrustLevel.UNVERIFIED)

    # السماح لا يكون إلا صريحاً
    guard.allow_unverified = True
    guard.check_provider("newprov", TrustLevel.UNVERIFIED)


def test_quota_enforces_model_call_limit():
    guard = QuotaGuard(max_model_calls=2)
    guard.consume_model("gemini")
    guard.consume_model("gemini")
    with pytest.raises(QuotaExceeded):
        guard.consume_model("gemini")

    snapshot = guard.snapshot()
    assert snapshot["usage"]["model_calls"] == 2
    assert snapshot["remaining"]["model_calls"] == 0


def _free(provider: str, model: str = "m", **kwargs) -> ModelInfo:
    """نموذج معلن أنه مجاني متحقَّق — الافتراضي في `ModelInfo` غير متحقَّق."""
    return ModelInfo(provider=provider, model=model, trust=TrustLevel.VERIFIED_FREE, **kwargs)


def _router(models, calls, quota=None):
    async def call(info, prompt, output_format):
        calls.append(info.provider)
        if info.provider == "broken":
            raise RuntimeError("عطل")
        return f"{info.provider}:ok"

    return CapabilityRouter(models, call, quota=quota)


def test_capability_router_prefers_verified_and_fails_over():
    calls: list[str] = []
    models = [
        _free("broken", "b", priority=1),
        ModelInfo(provider="good", model="g", trust=TrustLevel.UNVERIFIED, priority=2),
    ]
    router = _router(models, calls, quota=QuotaGuard(allow_unverified=True))

    assert [info.provider for info in router.eligible()] == ["broken", "good"]
    assert asyncio.run(router.complete("مرحبا")) == "good:ok"
    assert calls == ["broken", "good"]


def test_capability_router_respects_capability_and_policy():
    models = [
        _free("text", "t", capabilities=frozenset({Capability.REASONING})),
        _free("coder", "c", capabilities=frozenset({Capability.CODING})),
        ModelInfo(provider="paid", model="p", trust=TrustLevel.PAID),
    ]
    router = _router(models, [])

    assert [info.provider for info in router.eligible(Capability.CODING)] == ["coder"]
    assert "paid" not in [info.provider for info in router.available()]
    with pytest.raises(NoEligibleModel):
        asyncio.run(router.complete("x", capability=Capability.VISION))


def test_capability_router_consumes_quota_and_stops():
    models = [_free("a", "1"), _free("b", "2")]
    quota = QuotaGuard(max_model_calls=1)
    router = _router(models, [], quota=quota)
    assert asyncio.run(router.complete("x")) == "a:ok"
    with pytest.raises(NoEligibleModel):
        asyncio.run(router.complete("x"))


def test_model_router_protocol_is_satisfied():
    router = _router([_free("a", "1")], [])
    assert isinstance(router, ModelRouter)
    assert asyncio.run(router.complete("hi")) == "a:ok"


# ================= 4) المحرك: الدورة الكاملة =================

def test_engine_full_cycle_is_verified(workspace: Path):
    """الدورة الكاملة: طلب ← أداة سياق ← تخطيط ← تنفيذ ← تحقق."""
    engine, policy, _, _ = make_engine(workspace)
    request = TaskRequest(
        task="لخّص ملف README",
        context_tools=[ToolCall(name="read_file", args={"path": "README.md"}, purpose="سياق")],
    )
    result = asyncio.run(engine.run(request))

    assert result.status is TaskStatus.COMPLETED
    assert result.verified is True
    assert UNVERIFIED_NOTE not in result.answer
    assert any(item.kind == "tool" and "QuotaGuard" in item.content for item in result.evidence)
    assert any(item.kind == "step" for item in result.evidence)
    assert all(check.passed for check in result.checks)
    assert result.tool_calls == 1
    # الدليل وصل فعلاً إلى prompt المخطط
    assert any("QuotaGuard" in prompt for prompt in engine.router.prompts)
    # وكل قرار في سجل التدقيق
    assert any(entry.tool == "read_file" for entry in policy.audit.entries())


def test_engine_marks_unverified_when_steps_fail(workspace: Path):
    """لا نجاح بلا دليل: خطوة فاشلة ⇒ UNVERIFIED + تنبيه صريح في الإجابة."""
    engine, _, _, _ = make_engine(
        workspace, responses=FAILING_STEPS, memory=None
    )
    result = asyncio.run(engine.run(TaskRequest(task="مهمة صعبة")))

    assert result.status is TaskStatus.UNVERIFIED
    assert result.verified is False
    assert UNVERIFIED_NOTE.strip() in result.answer
    assert any("نجحت كل الخطوات" in check.name and not check.passed for check in result.checks)
    assert result.failed_checks


def test_engine_blocks_when_context_tool_is_denied(workspace: Path, tmp_path: Path):
    """أداة سياق مرفوضة ⇒ المهمة تُنفَّذ لكنها لا تُعلَن ناجحة."""
    engine, _, _, _ = make_engine(workspace)
    request = TaskRequest(
        task="اقرأ ملفاً خارج المسموح",
        context_tools=[ToolCall(name="read_file", args={"path": "/etc/passwd"})],
    )
    result = asyncio.run(engine.run(request))

    assert result.status is TaskStatus.UNVERIFIED
    assert any(item.kind == "tool" and not item.ok for item in result.evidence)
    assert any("أدوات السياق" in check.name and not check.passed for check in result.checks)


def test_engine_blocked_when_budget_exhausted(workspace: Path):
    engine, _, _, quota = make_engine(workspace)
    quota.max_model_calls = 0
    result = asyncio.run(engine.run(TaskRequest(task="أي مهمة")))

    assert result.status is TaskStatus.BLOCKED
    assert "الميزانية" in result.blocked_reason
    assert engine.router.calls == 0


def test_engine_blocked_when_no_model_allowed(workspace: Path):
    class NoModelsRouter(ScriptedRouter):
        def available(self):
            return []

    engine, _, _, _ = make_engine(workspace)
    engine.router = NoModelsRouter(HAPPY)
    result = asyncio.run(engine.run(TaskRequest(task="مهمة")))

    assert result.status is TaskStatus.BLOCKED
    assert "سياسة التكلفة" in result.blocked_reason


def test_engine_failed_when_provider_crashes(workspace: Path):
    engine, _, _, _ = make_engine(workspace, responses=HAPPY)
    engine.router = ScriptedRouter(HAPPY, fail_at=1)
    result = asyncio.run(engine.run(TaskRequest(task="مهمة تنهار")))

    assert result.status is TaskStatus.FAILED
    assert "RuntimeError" in result.error


def test_engine_requires_min_average_score(workspace: Path):
    engine, _, _, _ = make_engine(workspace)
    result = asyncio.run(engine.run(TaskRequest(task="مهمة", min_average_score=10)))

    assert result.status is TaskStatus.UNVERIFIED
    assert any("متوسط التقييم" in check.name and not check.passed for check in result.checks)


def test_engine_can_relax_verification_requirements(workspace: Path):
    """المستخدم يملك قرار شروط القبول — لكن الحالة تبقى صادقة."""
    engine, _, _, _ = make_engine(workspace, responses=FAILING_STEPS)
    relaxed = TaskRequest(
        task="مهمة", require_all_steps=False, require_evidence=False, min_average_score=0
    )
    result = asyncio.run(engine.run(relaxed))

    assert result.status is TaskStatus.COMPLETED
    assert result.verified is True
    assert not any("نجحت كل الخطوات" in check.name for check in result.checks)


def test_engine_summary_is_human_readable(workspace: Path):
    engine, _, _, _ = make_engine(workspace)
    result = asyncio.run(engine.run(TaskRequest(task="مهمة")))
    summary = result.summary()
    assert "✅" in summary
    assert "الشروط" in summary


# ================= 5) واجهة CLI للعقود =================

def test_cli_contract_flags_parse():
    """كل وسائط العقود معرّفة فعلاً — يمنع AttributeError وقت التشغيل."""
    from src.main import build_parser

    args = build_parser().parse_args(
        [
            "--task", "مهمة",
            "--read", "a.md", "--read", "b.md",
            "--search", "QuotaGuard",
            "--allow-root", "/tmp",
            "--grant", "shell.exec",
            "--yes",
            "--max-model-calls", "5",
            "--max-tool-calls", "2",
            "--allow-unverified-provider",
            "--require-verified",
        ]
    )
    assert args.read == ["a.md", "b.md"]
    assert args.search == "QuotaGuard"
    assert args.grant == ["shell.exec"]
    assert args.yes is True
    assert args.max_model_calls == 5
    assert args.max_tool_calls == 2
    assert args.require_verified is True

    defaults = build_parser().parse_args(["--task", "م"])
    assert defaults.read is None and defaults.search is None
    assert defaults.max_model_calls == 40 and defaults.max_tool_calls == 20
    assert defaults.yes is False and defaults.require_verified is False


def test_cli_builds_request_policy_and_quota(tmp_path: Path):
    """الوسائط تتحوّل إلى عقود صحيحة (طلب/سياسة/ميزانية)."""
    from src.main import build_parser, build_policy, build_quota, build_request

    args = build_parser().parse_args(
        [
            "--task", "لخّص", "--read", "notes.md", "--search", "قرار",
            "--allow-root", str(tmp_path), "--grant", "shell.exec",
            "--max-model-calls", "3", "--max-tool-calls", "1",
        ]
    )

    request = build_request(args)
    assert request.task == "لخّص"
    assert [call.name for call in request.context_tools] == ["read_file", "search_files"]
    assert request.context_tools[0].args == {"path": "notes.md"}
    assert request.context_tools[1].args == {"query": "قرار"}

    policy = build_policy(args)
    assert policy.allowed_roots == (tmp_path.resolve(),)
    assert "shell.exec" in policy.granted_scopes and "fs.read" in policy.granted_scopes

    quota = build_quota(args)
    assert quota.max_model_calls == 3 and quota.max_tool_calls == 1


def test_cli_run_agent_returns_task_result(tmp_path: Path, monkeypatch):
    """`run_agent` في نمط reason يمرّر طلباً بعقد المهمة ويعيد `TaskResult`."""
    import asyncio

    from src import main as cli
    from src.core.task import TaskResult

    (tmp_path / "notes.md").write_text("قرار: SQLite\n", encoding="utf-8")
    captured: dict = {}
    signals: list[str] = []

    class FakeRouter:
        def __init__(self, *args, **kwargs):
            self.settings = type(
                "S",
                (),
                {
                    "agent_max_steps": 5,
                    "agent_memory_path": None,
                    "agent_memory_top_k": 4,
                    "agent_memory_min_score": 0.05,
                    "agent_runs_path": str(tmp_path / "runs"),
                    "agent_runs_keep": 5,
                },
            )()

        def require_any_provider(self):
            signals.append("checked")

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            captured["engine_kwargs"] = kwargs

        async def run(self, request):
            captured["request"] = request
            return TaskResult(status=TaskStatus.COMPLETED, answer="تم", verified=True)

    monkeypatch.setattr(cli, "LLMRouter", FakeRouter)
    monkeypatch.setattr(cli, "AgentEngine", FakeEngine)

    args = cli.build_parser().parse_args(
        ["--task", "لخّص الملاحظات", "--allow-root", str(tmp_path), "--read", "notes.md"]
    )
    result = asyncio.run(cli.run_agent(args))

    assert signals == ["checked"]
    assert isinstance(result, TaskResult) and result.verified
    request = captured["request"]
    assert request.task == "لخّص الملاحظات"
    assert [call.name for call in request.context_tools] == ["read_file"]
    # السجل والسياسة والميزانية وُصلت بالمحرك فعلاً
    assert captured["engine_kwargs"]["registry"] is not None
    assert captured["engine_kwargs"]["quota"] is not None

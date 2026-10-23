"""
سجل الأدوات (Tool Registry)
===========================
عقد موحّد لكل أداة يستطيع الوكيل استدعاءها:

  • `ToolSpec`  — الاسم، الوصف، نموذج الوسائط (Pydantic)، النطاق، الخطر، والمعالج.
  • `ToolResult` — نتيجة منظّمة: نجحت/فشلت، المخرجات، سبب الرفض، ومدة التنفيذ.
  • `ToolRegistry` — تسجيل وبحث واستدعاء، مع **تحقق وسائط إلزامي** واحترام
    السياسة والميزانية قبل أي تنفيذ.

لا توجد أداة «سحرية»: كل استدعاء يمر عبر:
    وجود الأداة ← تحقق الوسائط ← حصر المسارات ← السياسة/الموافقة ← الميزانية ← التنفيذ
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from src.core.policy import AuditLog, PolicyDenied, PolicyEngine, ToolRisk
from src.core.quota import QuotaExceeded, QuotaGuard

logger = logging.getLogger("new-cela.tools")


class ToolContext:
    """كل ما تحتاجه الأداة دون أن تعرف شيئاً عن بقية النظام."""

    def __init__(
        self,
        policy: PolicyEngine,
        quota: QuotaGuard | None = None,
        workspace: Any = None,
        timeout: int = 30,
    ) -> None:
        self.policy = policy
        self.quota = quota or QuotaGuard()
        self.workspace = workspace
        self.timeout = timeout


# معالج الأداة: نص للأدوات العادية أو ToolResult للأدوات ذات القياس المنظّم.
ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    """تعريف أداة — عقدها الكامل في مكان واحد."""

    name: str
    description: str
    params: type[BaseModel]
    handler: ToolHandler
    risk: ToolRisk = ToolRisk.READ
    scope: str = "fs.read"
    path_params: tuple[str, ...] = ()
    examples: tuple[str, ...] = field(default=())

    def schema(self) -> dict[str, Any]:
        """مخطط الإدخال (JSON Schema) — جاهز للعرض أو لتعريف دالة للنموذج."""
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "scope": self.scope,
            "input_schema": self.params.model_json_schema(),
        }


class ToolResult(BaseModel):
    """نتيجة استدعاء أداة — منظّمة وقابلة للتسجيل كدليل."""

    name: str
    ok: bool
    output: str = ""
    error: str = ""
    denied: bool = False
    reason: str = ""
    duration_ms: int = 0
    # قياسات اختيارية للأدوات التنفيذية؛ لا تُستنتج من نص الإجابة.
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    def as_evidence(self) -> str:
        """نص قصير يُسجَّل كدليل في نتيجة المهمة."""
        if self.ok:
            return f"{self.name}: {self.output[:400]}"
        return f"{self.name}: {'رُفض' if self.denied else 'فشل'} — {self.error or self.reason}"


class ToolRegistry:
    """
    سجل الأدوات — المصدر الوحيد لمعرفة «ما يستطيع الوكيل فعله».

    `invoke` لا يرفع استثناءات التنفيذ أبداً: يعيد `ToolResult` فاشلاً،
    لأن فشل أداة يجب أن يكون **معلومة** للوكيل لا انهياراً للمهمة.
    """

    def __init__(self, policy: PolicyEngine | None = None, quota: QuotaGuard | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self.policy = policy or PolicyEngine()
        self.quota = quota or QuotaGuard()

    # ---------------- التسجيل ----------------
    def register(self, spec: ToolSpec) -> ToolSpec:
        if not spec.name or not spec.name.isidentifier():
            raise ValueError(f"اسم أداة غير صالح: {spec.name!r}")
        if spec.name in self._tools:
            raise ValueError(f"أداة مكرّرة الاسم: {spec.name}")
        self._tools[spec.name] = spec
        logger.debug("سُجّلت أداة: %s (%s)", spec.name, spec.risk.value)
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [self._tools[name] for name in sorted(self._tools)]

    def describe(self) -> list[dict[str, Any]]:
        """وصف كل الأدوات — يُمرَّر للنموذج أو يُعرض في الواجهة."""
        return [spec.schema() for spec in self.specs()]

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    # ---------------- الاستدعاء ----------------
    async def invoke(
        self,
        name: str,
        args: dict | None = None,
        context: ToolContext | None = None,
        purpose: str = "",
    ) -> ToolResult:
        """
        ينفّذ أداة بعد كل الفحوص. يعيد نتيجة موصوفة بدل رفع الاستثناء.
        """
        started = time.monotonic()
        spec = self._tools.get(name)
        if spec is None:
            return _result(name, started, error=f"أداة غير معروفة: {name}")

        ctx = context or ToolContext(policy=self.policy, quota=self.quota)

        # 1) تحقق الوسائط
        try:
            params = spec.params.model_validate(args or {})
        except ValidationError as exc:
            return _result(
                name,
                started,
                error="وسائط غير صالحة: " + _short_validation(exc),
                reason="validation",
            )

        # 2) حصر المسارات المعلنة (دفاع إضافي قبل السياسة)
        for field_name in spec.path_params:
            value = getattr(params, field_name, None)
            if value is None:
                continue
            try:
                ctx.policy.resolve_path(value)
            except PolicyDenied as exc:
                return _denied(name, started, str(exc))

        # 3) السياسة والموافقة
        try:
            await ctx.policy.authorize(
                tool=spec.name,
                risk=spec.risk,
                scope=spec.scope,
                args=params.model_dump(mode="json"),
                summary=f"{spec.description} — {purpose}".strip(" —"),
            )
        except PolicyDenied as exc:
            return _denied(name, started, str(exc))

        # 4) الميزانية
        try:
            ctx.quota.consume_tool(spec.name)
        except QuotaExceeded as exc:
            ctx.policy.record_outcome(spec.name, spec.risk, spec.scope, ok=False, note=str(exc))
            return _result(name, started, error=str(exc), reason="quota")

        # 5) التنفيذ
        try:
            output = await spec.handler(params, ctx)
        except Exception as exc:  # noqa: BLE001 — فشل الأداة معلومة لا انهيار
            message = f"{type(exc).__name__}: {exc}"
            ctx.policy.record_outcome(spec.name, spec.risk, spec.scope, ok=False, note=message)
            logger.warning("فشل تنفيذ الأداة %s (%s).", name, type(exc).__name__)
            return _result(name, started, error=message)

        if isinstance(output, ToolResult):
            result = output.model_copy(
                update={
                    "name": spec.name,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            ctx.policy.record_outcome(
                spec.name,
                spec.risk,
                spec.scope,
                ok=result.ok,
                note=result.error or result.reason,
            )
            return result

        ctx.policy.record_outcome(spec.name, spec.risk, spec.scope, ok=True)
        return _result(name, started, output=str(output))


def build_context(
    registry: ToolRegistry,
    policy: PolicyEngine,
    quota: QuotaGuard | None = None,
    workspace: Any = None,
) -> ToolContext:
    """اختصار: سياق جاهز مرتبط بسجل وسياسة."""
    return ToolContext(policy=policy, quota=quota or registry.quota, workspace=workspace)


def default_audit_log(path: str | None = None) -> AuditLog:
    """سجل تدقيق افتراضي (ملف اختياري)."""
    return AuditLog(path=path)


def _result(
    name: str,
    started: float,
    output: str = "",
    error: str = "",
    reason: str = "",
) -> ToolResult:
    return ToolResult(
        name=name,
        ok=not error,
        output=output,
        error=error,
        reason=reason,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _denied(name: str, started: float, reason: str) -> ToolResult:
    return ToolResult(
        name=name,
        ok=False,
        denied=True,
        reason=reason,
        error=reason,
        duration_ms=int((time.monotonic() - started) * 1000),
    )


def _short_validation(exc: ValidationError, limit: int = 240) -> str:
    parts = [
        f"{'.'.join(str(loc) for loc in error['loc']) or 'args'}: {error['msg']}"
        for error in exc.errors()[:3]
    ]
    text = "; ".join(parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"

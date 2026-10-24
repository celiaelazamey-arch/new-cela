"""فاحص انحراف المنطق عن المواصفة (Failure Pattern 4: Logic Drift).

الخطر: يعامل الوكيل مخرجاته السابقة كأنها المصدر الأصلي، فتتراكم الأخطاء
دون ملاحظة. العلاج: **مقارنة دورية مع متطلبات أصلية ثابتة** (spec).

هذا الفاحص يثبّت «عقوداً معمارية» لا يجوز أن تنحرف: الدوال/الثوابت/الملفات
التي يعتمد عليها باقي النظام. لو تغيّر اسم أو اختفى عقد ⇒ يفشل البناء ويطلب
مراجعة بشرية بدل أن يكتشف المستخدم متأخراً.

كما يتحقق من ملف القواعد (CLAUDE.md) ومراجع السياسات — لأن غيابها هو
Failure Pattern 12 (غياب meta-instructions).

مجاني 100% — المكتبة القياسية فقط (ast). الاستخدام:
    python scripts/spec_check.py
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# العقود المعمارية: {ملف: {اسم: نوع}} — يجب أن تبقى موجودة.
# أي تغيير هنا نفسه يحتاج مراجعة بشرية (هذا هو مغزى الملف).
CONTRACTS: dict[str, dict[str, str]] = {
    "src/providers/llm_router.py": {
        "LLMRouter": "class",
        "CircuitBreaker": "class",
        "execute_reasoning_step": "func",
        "execute_structured_step": "func",
        "get_chat_model": "func",
        "health": "func",
        "GEMINI": "const",
        "GROQ": "const",
    },
    "src/core/model_api.py": {
        "ModelInfo": "class",
        "CapabilityRouter": "class",
        "router_from_llm": "func",
    },
    "src/core/policy.py": {
        "PolicyEngine": "class",
        "PolicyDenied": "class",
        "DEFAULT_SCOPES": "const",
        "AutonomyLevel": "class",
        "KillSwitch": "class",
        "IRREVERSIBLE_SCOPES": "const",
    },
    # المهمة 11 — الوعي الوظيفي: تقادم نموذج الذات، معايرة، مقاييس، محكّم.
    "src/awareness/self_model.py": {
        "Capability": "class",
        "SelfModel": "class",
        "DriftFinding": "class",
        "DEFAULT_TTL_DAYS": "const",
    },
    "src/awareness/calibration.py": {
        "CalibrationLedger": "class",
        "gate": "func",
        "DEFAULT_THRESHOLD": "const",
        "REFUSE_THRESHOLD": "const",
    },
    "src/awareness/metrics.py": {
        "compute_metrics": "func",
        "promotion_check": "func",
    },
    "src/core/arbiter.py": {
        "Arbiter": "class",
    },
    "src/core/quota.py": {
        "QuotaGuard": "class",
        "TrustLevel": "class",
    },
    "src/execution/sandbox.py": {
        "SandboxExecutor": "class",
    },
    "src/core/path_guard.py": {
        "guard_path": "func",
        "guard_run_id": "func",
        "sanitize_for_log": "func",
    },
    "src/core/prompt_guard.py": {
        "PromptRiskLevel": "class",
        "PromptGuardResult": "class",
        "PromptInjectionDetected": "class",
        "inspect_prompt_injection": "func",
        "guard_tool_input": "func",
        "strip_invisible_chars": "func",
    },
    "src/core/pricing.py": {
        "PlanTier": "class",
        "PlanSpec": "class",
        "PLAN_CATALOG": "const",
        "build_plan_guard": "func",
        "evaluate_usage_alert": "func",
        "calculate_unit_economics": "func",
        "render_pricing_table": "func",
    },
    "src/core/niche_strategy.py": {
        "VerticalNiche": "class",
        "NicheWorkflowSpec": "class",
        "NinetyDayDecision": "class",
        "NinetyDayEvaluation": "class",
        "NICHE_CATALOG": "const",
        "get_niche_spec": "func",
        "validate_positioning_claim": "func",
        "evaluate_90_day_gate": "func",
        "render_niche_playbook": "func",
    },
    "src/screen_print/engine.py": {
        "FabricShade": "class",
        "InkType": "class",
        "FilmQCStatus": "class",
        "FilmSetRecord": "class",
        "FilmArchiveStore": "class",
        "FilmInspectionReport": "class",
        "ScreenJobEstimate": "class",
        "inspect_film_image": "func",
        "recommend_mesh_and_lpi": "func",
        "calculate_screen_print_job": "func",
    },
    "src/core/playbook.py": {
        "ProductStage": "class",
        "GoldenRuleEvaluation": "class",
        "BackupManifest": "class",
        "ProjectHealthAction": "class",
        "ActivityGateResult": "class",
        "PLAYBOOK_18_EVIDENCE": "const",
        "evaluate_golden_rule_stage": "func",
        "create_verified_backup": "func",
        "restore_and_verify_backup": "func",
        "evaluate_30_day_activity_gate": "func",
        "audit_18_point_playbook": "func",
    },
    "src/ingestion/pipeline.py": {
        "IngestionStatus": "class",
        "IngestionLimits": "class",
        "IngestionResult": "class",
        "IngestionPipeline": "class",
        "sanitize_filename": "func",
        "detect_magic_mime": "func",
        "detect_and_decode_text": "func",
        "sanitize_svg_or_html": "func",
        "ingest_file": "func",
    },
    "scripts/dependency_guard.py": {"main": "func"},
    "scripts/secret_scan.py": {"main": "func"},
    "scripts/pii_scan.py": {"main": "func"},
    "scripts/error_guard.py": {"main": "func"},
    "scripts/policy_guard.py": {"main": "func"},
    "scripts/action_pin_guard.py": {"main": "func"},
}

# ملفات مرجعية يجب أن تظل موجودة (meta-instructions + أدلة).
REQUIRED_DOCS = (
    "CLAUDE.md",
    "AGENT_HANDOFF.md",
    "SECURITY.md",
    "docs/security-threat-model.md",
    "docs/ai-code-reliability.md",
    "docs/agent-failure-patterns.md",
    "docs/ci-cd-free-tier.md",
    "docs/compliance-framework.md",
    "docs/functional-awareness-qa.md",
    "docs/monetization-plan.md",
    "docs/devsecops-free-tools.md",
    "docs/niche-strategy-manus.md",
    "docs/operating-playbook-18.md",
    "docs/file-ingestion-pipeline.md",
    "CHANGELOG.md",
)

# بنود المواصفة الأصلية (من الطلبات الأولى) — تُذكَّر في التقرير لمنع النسيان.
ORIGINAL_REQUIREMENTS = (
    "لا فقدان لأي شيء بين الجلسات (AGENT_HANDOFF + git push)",
    "الاعتماد على الموارد المجانية فقط في البداية",
    "وكيل يتعامل مع المستودع ويشرح التنبيهات رغم 403",
    "قاعدة: لا دخول في مهمة قبل إنهاء وتأكيد سابقتها",
    "كل طلب يُسجَّل مع إثبات تنفيذ",
    "Generate ← Run ← Test ← Review ← Commit",
)


def _top_level_names(path: pathlib.Path) -> dict[str, str] | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None
    names: dict[str, str] = {}
    for node in tree.body:  # التعريفات على المستوى الأعلى
        if isinstance(node, ast.ClassDef):
            names[node.name] = "class"
            # ✅ methods داخل الأصناف عقود أيضاً (execute_*/health/... ).
            # تجاهلها كان سيجعل الفاحص يدّعي اختفاء دوال موجودة فعلاً.
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.setdefault(child.name, "func")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names[node.name] = "func"
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    names[target.id] = "const"
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                names[node.target.id] = "const"
    return names


def main() -> int:
    print("=" * 74)
    print("  فاحص انحراف المنطق — مقارنة الكود بالمواصفة (Failure Pattern 4)")
    print("=" * 74)
    print(f"  المفسّر: {sys.executable}")

    violations: list[str] = []

    # 1) العقود المعمارية
    for rel, expected in CONTRACTS.items():
        path = ROOT / rel
        names = _top_level_names(path)
        if names is None:
            violations.append(f"{rel}: غير موجود أو لا يُحلَّل — العقد مفقود.")
            continue
        for name, kind in expected.items():
            if name not in names:
                violations.append(f"{rel}: العقد «{name}» ({kind}) اختفى — انحراف عن المواصفة.")
            elif kind in ("class", "func") and names[name] != kind:
                violations.append(f"{rel}: «{name}» تغيّر نوعه إلى {names[name]} (كان {kind}).")

    # 2) المراجع
    for rel in REQUIRED_DOCS:
        if not (ROOT / rel).is_file():
            violations.append(f"مرجع مفقود: {rel} — غياب meta-instructions (البند 12).")

    print(f"  عقود مُتحقَّقة: {sum(len(v) for v in CONTRACTS.values())}")
    print(f"  مراجع مطلوبة: {len(REQUIRED_DOCS)}")
    print("\n  📜 المتطلبات الأصلية (مرجع ثابت — لا تُستبدل بمخرجات سابقة):")
    for i, req in enumerate(ORIGINAL_REQUIREMENTS, 1):
        print(f"     {i}. {req}")

    if not violations:
        print("\n  ✅ الكود مطابق للمواصفة. لم يُرصد انحراف منطقي.")
        print("=" * 74)
        return 0

    print(f"\n  ❌ انحرافات ({len(violations)}):")
    for v in violations:
        print(f"     • {v}")
    print("\n  لا تُصلح الانحراف بتغيير المواصفة — راجع المصدر الأصلي أولاً.")
    print("=" * 74)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

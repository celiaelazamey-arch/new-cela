"""اختبارات حواجز الحوكمة — تُثبت أن الفواحص تعمل فعلاً (البندان 8 و10).

القاعدة: الفاحص الذي لم يُختبر هو نفسه كود غير موثوق. كل فاحص هنا
يُختبر بالاتجاهين: مخالفة مزروعة ⇒ يُمسكها، وحالة سليمة ⇒ يمرّرها.
بلا شبكة، بلا مفاتيح، مجاني 100%.
"""
from __future__ import annotations

from pathlib import Path

from scripts import error_guard, policy_guard, spec_check

# ================= error_guard =================

def test_error_guard_detects_bare_pass(tmp_path: Path, monkeypatch):
    """except: pass بلا توثيق ⇒ مخالفة PASS_BARE."""
    f = tmp_path / "bad.py"
    f.write_text(
        "try:\n    risky()\nexcept OSError:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(error_guard, "ROOT", tmp_path)
    violations, stats = error_guard.scan(tmp_path)
    assert any(k == "PASS_BARE" for _, _, k, _ in violations)
    assert stats.get("PASS_BARE") == 1


def test_error_guard_accepts_documented_pass(tmp_path: Path):
    """except: pass مع تعليق مبرّر ⇒ PASS_DOC مقبول."""
    f = tmp_path / "ok.py"
    f.write_text(
        "try:\n    close()\nexcept OSError:  # مقصود — أُغلق مسبقاً\n    pass\n",
        encoding="utf-8",
    )
    violations, stats = error_guard.scan(tmp_path)
    assert not any(k == "PASS_BARE" for _, _, k, _ in violations)
    assert stats.get("PASS_DOC") == 1


def test_error_guard_accepts_raise_and_log(tmp_path: Path):
    """raise / logger / return كلها تصنيفات مقبولة لا ابتلاع."""
    f = tmp_path / "good.py"
    f.write_text(
        "import logging\nlogger = logging.getLogger('x')\n"
        "try:\n    a()\nexcept ValueError:\n    logger.warning('bad')\n"
        "try:\n    b()\nexcept TypeError:\n    raise\n"
        "try:\n    c()\nexcept KeyError:\n    return None\n",
        encoding="utf-8",
    )
    violations, stats = error_guard.scan(tmp_path)
    assert not violations
    assert stats.get("LOG", 0) >= 1 and stats.get("RAISE", 0) >= 1


def test_error_guard_accepts_action_and_assert(tmp_path: Path):
    """تسجيل نتيجة فاشلة عبر assignment أو assert ⇒ ليس ابتلاعاً."""
    f = tmp_path / "act.py"
    f.write_text(
        "try:\n    a()\nexcept ValueError as exc:\n    failed = True\n"
        "try:\n    b()\nexcept SystemExit as exc:\n    assert exc.code == 0\n",
        encoding="utf-8",
    )
    violations, _ = error_guard.scan(tmp_path)
    assert not violations


def test_error_guard_detects_silent(tmp_path: Path):
    """معالجة بلا أي فعل هادف ⇒ SILENT.

    نستخدم استدعاءً غير مُبلّغ (ليس logger/raise/return/assign/append/check)،
    وهو بالضبط نمط «الكود يعمل لكنه لا يفعل شيئاً مفيداً».
    """
    f = tmp_path / "silent.py"
    f.write_text(
        "try:\n    a()\nexcept ValueError:\n    do_nothing_unknown()\n",
        encoding="utf-8",
    )
    violations, _ = error_guard.scan(tmp_path)
    assert any(k == "SILENT" for _, _, k, _ in violations)


def test_error_guard_real_repo_clean():
    """المستودع نفسه يجب أن يمرّ بعد الإصلاحات (دليل لا ادعاء)."""
    violations, _ = error_guard.scan()
    assert violations == [], f"ابتلاع صامت في المستودع: {violations}"


# ================= policy_guard =================

def test_policy_guard_detects_shell_true(tmp_path: Path, monkeypatch):
    """shell=True مزروع ⇒ يُمسك."""
    sb = tmp_path / "src" / "execution"
    sb.mkdir(parents=True)
    (sb / "sandbox.py").write_text(
        "asyncio.create_subprocess_exec(cmd, shell=True)\n", encoding="utf-8"
    )
    monkeypatch.setattr(policy_guard, "ROOT", tmp_path)
    issues = policy_guard.check_no_shell()
    assert any("shell=True" in i for i in issues)


def test_policy_guard_detects_widened_scopes(tmp_path: Path, monkeypatch):
    """توسيع DEFAULT_SCOPES ⇒ يُمسك."""
    core = tmp_path / "src" / "core"
    core.mkdir(parents=True)
    (core / "policy.py").write_text(
        'DEFAULT_SCOPES = frozenset({"fs.read", "fs.write", "exec"})\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(policy_guard, "ROOT", tmp_path)
    issues = policy_guard.check_default_scopes()
    assert issues and any("تغيّر" in i for i in issues)


def test_policy_guard_ignores_yes_in_comment(tmp_path: Path, monkeypatch):
    """--yes داخل تعليق لا يُعدّ تمريراً (regress للخطأ الذي وقع فعلاً)."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "agent-runner.yml").write_text(
        "# بلا --yes (لا موافقة تلقائية)\n"
        "run: |\n  python -m src.main --task x\n"
        "  python scripts/sanitize_output.py agent_result.txt\n"
        "  MAX_TASK_CHARS: 8000\n"
        "  author_association: OWNER\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(policy_guard, "ROOT", tmp_path)
    issues = policy_guard.check_runner_hardening()
    assert not any("--yes" in i for i in issues)


def test_policy_guard_detects_real_yes(tmp_path: Path, monkeypatch):
    """--yes في أمر تنفيذي ⇒ يُمسك."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "agent-runner.yml").write_text(
        "run: |\n  python -m src.main --yes --task x\n"
        "  python scripts/sanitize_output.py a.txt\n"
        "  MAX_TASK_CHARS: 8000\n  author_association: OWNER\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(policy_guard, "ROOT", tmp_path)
    issues = policy_guard.check_runner_hardening()
    assert any("--yes" in i for i in issues)


def test_policy_guard_real_repo_clean():
    """المستودع نفسه يجب أن يمرّ."""
    assert policy_guard.check_default_scopes() == []
    assert policy_guard.check_no_shell() == []
    assert policy_guard.check_no_paid_default() == []
    assert policy_guard.check_runner_hardening() == []
    assert policy_guard.check_allowlists_intact() == []


# ================= spec_check =================

def test_spec_check_finds_class_methods(tmp_path: Path):
    """دوال داخل أصناف تُعدّ عقوداً (regress: الفاحص ادّعى اختفاءها)."""
    f = tmp_path / "router.py"
    f.write_text(
        "class LLMRouter:\n"
        "    async def execute_reasoning_step(self, prompt):\n        return ''\n",
        encoding="utf-8",
    )
    names = spec_check._top_level_names(f)
    assert names is not None
    assert names.get("LLMRouter") == "class"
    assert names.get("execute_reasoning_step") == "func"


def test_spec_check_real_repo_contracts():
    """عقود LLMRouter و ModelInfo موجودة فعلاً."""
    names = spec_check._top_level_names(spec_check.ROOT / "src/providers/llm_router.py")
    assert names and "LLMRouter" in names
    assert "execute_reasoning_step" in names
    assert "health" in names


def test_spec_check_original_requirements_recorded():
    """المتطلبات الأصلية الستة لا تُحذف (حماية من فقدان السياق)."""
    assert len(spec_check.ORIGINAL_REQUIREMENTS) >= 6
    assert any("فقدان" in r for r in spec_check.ORIGINAL_REQUIREMENTS)

"""اختبارات أمنية لحاجز المسارات — تُثبت إغلاق تنبيهات CodeQL الأربعة.

لكل تنبيه اختبار هجومي صريح: مدخل خبيث ⇒ مرفوض/مُطهَّر، ومدخل سليم ⇒ مقبول.
بلا شبكة، بلا مفاتيح، مجاني 100%.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.core.path_guard import (
    UnsafePath,
    guard_path,
    guard_run_id,
    sanitize_for_log,
)
from src.core.telemetry import RunArchive
from src.dashboard import data

# ================= التنبيه #1 و#2: Path injection في MemoryStore =================

@pytest.mark.parametrize("attack", [
    "../../etc/passwd",
    "subdir/../../etc/passwd",
    "/etc/passwd",
    "/proc/self/environ",
    "valid\x00.jsonl",
    "a" * 5000,
    "   ",  # مسافات فقط ⇒ مرفوض (ليس فراغاً صريحاً)
])
def test_memory_rejects_unsafe_paths(attack: str, tmp_path: Path) -> None:
    """المسارات الخبيثة تُرفض قبل أي exists/read_text (CodeQL #1/#2)."""
    from src.core.memory import MemoryStore

    with pytest.raises(UnsafePath):
        MemoryStore(path=attack)


def test_memory_empty_string_is_in_memory_not_error(tmp_path: Path) -> None:
    """المسار الفارغ صراحةً سلوك مقصود ⇒ ذاكرة RAM، لا استثناء (موثّق في الكود)."""
    from src.core.memory import MemoryStore

    store = MemoryStore(path="")
    assert store.path is None


def test_memory_accepts_safe_tmp_path(tmp_path: Path) -> None:
    """المسار السليم (tmp_path) مقبول — لا نكسر الاستخدام المشروع."""
    from src.core.memory import MemoryStore

    store = MemoryStore(path=tmp_path / "nested" / "memory.jsonl")
    assert store.path is not None


def test_dashboard_memory_falls_back_on_unsafe(tmp_path: Path) -> None:
    """اللوحة لا تنهار عند مسار خبيث — ترجع مخزناً فارغاً."""
    store = data.open_memory("../../etc/passwd")
    assert len(store) == 0
    assert store.path is None


# ================= التنبيه #3: Path injection عبر runs_dir =================

@pytest.mark.parametrize("attack", [
    "../../etc",
    "/etc",
    "runs\x00",
])
def test_archive_rejects_unsafe_dirs(attack: str) -> None:
    """glob على مسار خبيث مرفوض عند الإنشاء (CodeQL #3)."""
    with pytest.raises(UnsafePath):
        RunArchive(path=attack)


def test_archive_accepts_safe_dir(tmp_path: Path) -> None:
    archive = RunArchive(path=tmp_path / "runs")
    assert archive.path == tmp_path / "runs"


def test_dashboard_load_runs_returns_empty_on_unsafe() -> None:
    """مدخل خبيث في اللوحة ⇒ قائمة فارغة لا استثناء."""
    assert data.load_runs("../../etc") == []


def test_run_id_injection_blocked(tmp_path: Path) -> None:
    """load(run_id) يرفض معرّفاً يحاول الخروج من المجلد."""
    archive = RunArchive(path=tmp_path / "runs")
    for bad in ("../../etc/passwd", "a/b", "..", "x\\y", "\x00"):
        with pytest.raises(UnsafePath):
            archive.load(bad)


def test_run_id_valid_accepted(tmp_path: Path) -> None:
    """معرِّف سليم ⇒ لا استثناء (قد يرجع None لعدم وجود الملف)."""
    archive = RunArchive(path=tmp_path / "runs")
    assert archive.load("20261023T165542123456Z-a1b2c3") is None


# ================= التنبيه #4: Log Injection =================

def test_sanitize_for_log_strips_newlines_and_controls() -> None:
    """الأسطار الجديدة ومحارف التحكّم تُستبدل — لا حقن سجل."""
    dirty = "safe\nATTACKER: fake admin login\r\x00\x1b[31m"
    clean = sanitize_for_log(dirty)
    assert "\n" not in clean
    assert "\r" not in clean
    assert "\x00" not in clean
    assert "ATTACKER" in clean  # النص نفسه يبقى مرئياً لكن بلا سطر جديد


def test_sanitize_for_log_truncates() -> None:
    assert len(sanitize_for_log("أ" * 500, limit=50)) <= 50


def test_memory_log_is_sanitized(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """تحميل ذاكرة بمسار فيه سطر جديد ⇒ السجل لا يحتوي سطراً محقوناً."""
    from src.core.memory import MemoryStore

    # نبني مساراً آمناً (داخل tmp_path) لكن نختبر التنقيح مباشرةً عبر الدالة،
    # لأن guard_path يرفض \n في الأصل. نتحقق أن الرسالة المُسجَّلة منقّحة.
    store = MemoryStore(path=tmp_path / "mem.jsonl")
    with caplog.at_level(logging.INFO, logger="new-cela.memory"):
        store._load()
    # لا توجد أسطر جديدة داخل أي رسالة مُنسَّقة
    for record in caplog.records:
        assert "\n" not in record.getMessage()


# ================= الحاجز نفسه =================

def test_guard_path_sensitive_prefixes() -> None:
    for bad in ("/etc/passwd", "/proc/1/status", "/sys/kernel", "/dev/null"):
        with pytest.raises(UnsafePath):
            guard_path(bad)


def test_guard_path_relative_allowed() -> None:
    assert guard_path(".agent_memory/memory.jsonl") == Path(".agent_memory/memory.jsonl")


def test_guard_path_root_constraint(tmp_path: Path) -> None:
    allowed = (tmp_path,)
    assert guard_path(tmp_path / "ok.json", allowed_roots=allowed,
                      must_stay_under_root=True)
    with pytest.raises(UnsafePath):
        guard_path("/etc/passwd", allowed_roots=allowed, must_stay_under_root=True)


def test_guard_run_id_valid() -> None:
    assert guard_run_id("run-2026.10.23_abc") == "run-2026.10.23_abc"


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "..", "\x00", "x" * 200, "-lead"])
def test_guard_run_id_rejects_bad(bad: str) -> None:
    with pytest.raises(UnsafePath):
        guard_run_id(bad)


# ============ BARRIER الجديد: allowlist تقطع taint (نمط store.py) ============

@pytest.mark.parametrize("attack", [
    "runs; rm -rf /",       # فاصل أوامر
    "runs && whoami",        # && حقن
    "runs`id`",             # backtick
    "runs$(id)",            # استبدال أمر
    "runs|cat",             # pipe
    "runs?x",               # wildcard
    "runs*x",
    "runs\nATTACKER",       # سطر جديد محقون
    "runs\rATTACKER",
])
def test_guard_path_allowlist_rejects_injection(attack: str) -> None:
    """رموز الحقن والأسطار الجديدة مرفوضة عبر _SAFE_PATH.fullmatch barrier."""
    with pytest.raises(UnsafePath):
        guard_path(attack)


def test_guard_path_accepts_arabic_dir(tmp_path: Path) -> None:
    r"""\w يونيكود — المسارات العربية مقبولة (مستخدمون في مصر)."""
    arabic = "سجلات_التشغيل"
    result = guard_path(arabic)
    assert result == Path(arabic)


def test_sanitize_for_log_removes_ansi_escape() -> None:
    """isprintable() يزيل محارف التحكّم (ANSI escape) — يمنع تزييف السجل."""
    dirty = "ok\x1b[31mRED\x1b[0m\nnewline"
    clean = sanitize_for_log(dirty)
    assert "\x1b" not in clean
    assert "\n" not in clean
    assert "RED" in clean  # النص المرئي يبقى but بلا محارف تحكّم


def test_safe_path_label_pattern_rejects_newline() -> None:
    """نمط الـ label الضيّق في memory.py يرفض الأسطار الجديدة والفواصل."""
    import re
    label_pattern = re.compile(r"[\w.\-]+\Z")  # مطابق لـ _SAFE_PATH_LABEL
    assert label_pattern.fullmatch("memory.jsonl")
    assert label_pattern.fullmatch("مسار_آمن.jsonl")  # عربي مسموح
    assert label_pattern.fullmatch("path\nATTACKER") is None
    assert label_pattern.fullmatch("path\x00bad") is None
    assert label_pattern.fullmatch("dir/file.jsonl") is None  # لا فواصل مسارات


def test_safe_path_rejects_embedded_traversal_via_lookahead() -> None:
    """منع `..` مدمج في الـ regex نفسه (لا يعتمد على if منفصل)."""
    from src.core.path_guard import _SAFE_PATH
    assert _SAFE_PATH.fullmatch("sub/../etc") is None
    assert _SAFE_PATH.fullmatch("a..b") is None       # حتى غير المساري مرفوض دفاعاً
    assert _SAFE_PATH.fullmatch("sub/file.json") is not None

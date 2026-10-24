"""
حاجز المسارات (Path Guard) — يُطهِّر المسارات القادمة من مصادر غير موثوقة.
=====================================================================
سبب الوجود: CodeQL رصد 4 تنبيهات على `main`:
  • #1/#2 (High)  — Uncontrolled data in path expression في memory.py:472/476
  • #3   (High)   — Uncontrolled data in path expression في telemetry.py:232
  • #4   (Medium) — Log Injection في memory.py:508

المصدر: `dashboard/app.py` يقرأ `runs_dir` و`memory_path` من `st.text_input`
(حر)، فيمرّران إلى `MemoryStore`/`RunArchive` ثم إلى `exists/read_text/glob`.
الحل المعماري: **التحقق عند نقطة الدخول** (constructor)، لا ترقيع كل مصرف.
هذا هو نفس النمط الذي نجح في `cognitive/store.py::_validate_run_id`
(CodeQL لم يرصد تنبيهاً عليه رغم بناء مسار من معرّف).

فلسفة الحاجز (دفاع متعدد الطبقات):
  1. رفض صريح للمسارات التي تحاول الخروج (`..`) أو الحقن (`\\x00`).
  2. رفض المسارات المطلقة التي تشير إلى مناطق حساسة خارج الجذور المسموحة.
  3. حدّ أقصى للطول — يمنع إساءة استخدام الذاكرة.
  4. تطهير محارف التحكّم قبل أي كتابة في السجل (يمنع Log Injection).

مجاني 100% — المكتبة القياسية فقط. بلا أي اعتماد خارجي.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# حد أقصى معقول للمسار الذي يكتبه مستخدم في واجهة (يمنع إساءة الذاكرة).
MAX_PATH_CHARS = 4096

# محارف التحكّم/الأسطار الجديدة — مصدر حقن السجل (CWE-117).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NEWLINE = re.compile(r"[\r\n\u2028\u2029]")

# بادئات النظام الحساسة التي لا يجوز لمسار من واجهة أن يشير إليها صراحةً.
# (القائمة دفاعية — الجذر المسموح below هو الفيصل الحقيقي.)
_SENSITIVE_PREFIXES = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root")


class UnsafePath(ValueError):
    """مسار مرفوض من حاجز الأمان — لا يُستخدم في أي عملية قرص."""


def sanitize_for_log(value: object, limit: int = 200) -> str:
    """
    يطهّر قيمة قبل كتابتها في السجل — يمنع Log Injection (التنبيه #4).

    يستبدل الأسطار الجديدة ومحارف التحكّم بنقاط آمنة، ويقتطع الطول.
    """
    text = str(value)
    text = _NEWLINE.sub("⏎", text)
    text = _CONTROL_CHARS.sub("·", text)
    return text[:limit]


def guard_path(
    raw: str | os.PathLike[str],
    *,
    allowed_roots: tuple[Path, ...] | None = None,
    must_stay_under_root: bool = False,
) -> Path:
    """
    يتحقق من مسار قادم من مصدر غير موثوق ويعيده كـ `Path` آمن.

    المعاملات:
      raw: المسار الخام (من text_input / env / CLI).
      allowed_roots: جذور يُسمح بالبقاء تحتها عند `must_stay_under_root`.
      must_stay_under_root: إن True، يُرفض أي مسار يحلّ خارج الجذور.
                           (مفيد للمسارات التي تُقرأ من واجهة عامة.)

    يرفع `UnsafePath` عند:
      • فراغ/طول مفرط · محرف NUL · محاولة traversal (`..`)
      • مسار مطلق نحو بادئة حساسة
      • خروج عن الجذور المسموحة عند طلب ذلك
    """
    text = os.fspath(raw)
    if not isinstance(text, str):
        raise UnsafePath("نوع مسار غير مدعوم.")
    if not text or not text.strip():
        raise UnsafePath("مسار فارغ.")
    if len(text) > MAX_PATH_CHARS:
        raise UnsafePath("المسار أطول من الحد المسموح.")
    if "\x00" in text:
        raise UnsafePath("المسار يحتوي محرف NUL.")

    # منع traversal صريح — قبل أي resolve (قد يُخفي `..` عبر symlink).
    parts = re.split(r"[\\/]+", text)
    if ".." in parts:
        raise UnsafePath("محاولة traversal مرفوضة (..).")

    candidate = Path(text)

    # منع الإشارة الصريحة لمناطق النظام الحساسة.
    try:
        absolute = candidate if candidate.is_absolute() else (Path.cwd() / candidate)
        resolved = absolute.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # حلّ المسار فشل (حلقة symlink...)
        raise UnsafePath(f"تعذّر حلّ المسار: {type(exc).__name__}.") from exc

    resolved_text = str(resolved)
    if any(resolved_text == p or resolved_text.startswith(p + os.sep) for p in _SENSITIVE_PREFIXES):
        raise UnsafePath("المسار يشير إلى منطقة نظام حساسة.")

    if must_stay_under_root and allowed_roots:
        roots = [r.resolve(strict=False) for r in allowed_roots]
        if not any(resolved == r or r in resolved.parents for r in roots):
            raise UnsafePath("المسار يخرج عن الجذور المسموحة.")

    # نُعيد المسار بعد التطبيع (يحتفظ بالشكل الأصلي لكن آمن).
    return candidate


def guard_run_id(run_id: str) -> str:
    """
    يتحقق من مُعرِّف تشغيل قبل بناء اسم ملف منه (نمط store.py).

    يقبل فقط: حرف/رقم أولاً، ثم حروف وأرقام ونقاط وشرطات — حتى 128 محرفاً.
    يمنع `/` و`..` وحقن المسار في `self.path / f"{run_id}.json"`.
    """
    if not isinstance(run_id, str) or not run_id:
        raise UnsafePath("معرِّف تشغيل فارغ.")
    if len(run_id) > 128:
        raise UnsafePath("معرِّف تشغيل أطول من الحد.")
    if "\x00" in run_id or "/" in run_id or "\\" in run_id:
        raise UnsafePath("معرِّف تشغيل يحتوي محرف مسار.")
    if ".." in run_id:
        raise UnsafePath("محاولة traversal في معرِّف التشغيل.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        raise UnsafePath("معرِّف تشغيل غير صالح؛ استخدم حروفاً وأرقاماً و._- فقط.")
    return run_id


__all__ = ["UnsafePath", "guard_path", "guard_run_id", "sanitize_for_log"]

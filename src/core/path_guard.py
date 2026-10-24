"""حاجز المسارات (Path Guard) — يُطهِّر المسارات القادمة من مصادر غير موثوقة.
=====================================================================
سبب الوجود: CodeQL رصد تنبيهات path-injection/log-injection على `main`
(من `dashboard/app.py::st.text_input` → `MemoryStore`/`RunArchive` → عمليات قرص).

⚠️ التصميم مبني على **مصدر CodeQL الرسمي** لا على الحدس — راجع:
  python/ql/lib/semmle/python/security/dataflow/PathInjectionQuery.qll
  python/ql/lib/semmle/python/security/dataflow/PathInjectionCustomizations.qll
  python/ql/lib/semmle/python/security/dataflow/LogInjectionCustomizations.qll
  python/ql/lib/semmle/python/frameworks/Stdlib.qll

استنتاجات حاسمة من تلك المصادر:
  1. py/path-injection يستخدم flow-state: المسار يبدأ NotNormalized، ويصبح
     NormalizedUnchecked بعد os.path.abspath/normpath/realpath، ثم **يُقطع الـ taint
     فقط** عبر SafeAccessCheck = `str.startswith(...)` على المتغيّر المطبَّع بـ branch=true.
  2. **re.fullmatch ليس sanitizer** — الـ customizations تنصّ صراحة على عدم تعريض
     صنف Sanitizer، وتحيل إلى PathNormalization/SafeAccessCheck بدلاً منه.
  3. py/log-injection يعترف بـ `.replace("\\n", ...)` المباشر كـ sanitizer
     (ReplaceLineBreaksSanitizer) — لا re.sub ولا دالة خارجية.
  4. py/polynomial-redos: أي regex يُنفَّذ على قيمة مستخدم = sink. الحل: لا regex
     على المدخل الملوّث إطلاقاً؛ نستخدم str methods فقط (isalnum/split/replace).

مجاني 100% — المكتبة القياسية فقط. بلا أي اعتماد خارجي.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# حد أقصى معقول للمسار الذي يكتبه مستخدم في واجهة (يمنع إساءة الذاكرة).
MAX_PATH_CHARS = 4096

# محارف مسموحة صراحةً داخل المسار (يونيكود عبر isalnum ⇒ العربية مسموحة).
_ALLOWED_PUNCT = "._- /\\"


def _default_roots() -> tuple[str, ...]:
    """الجذور المسموح بها افتراضياً: مجلد المشروع + مجلد النظام المؤقت.

    يُسمح بـ tempdir حتى تعمل اختبارات pytest (tmp_path تحت /tmp) ومسارات العمل
    المؤقتة المشروعة. أي مسار نظام حساس (/etc, /proc, ...) يقع خارجها ⇒ مرفوض.
    """
    return (
        os.path.abspath(os.getcwd()),
        os.path.abspath(tempfile.gettempdir()),
    )


class UnsafePath(ValueError):
    """مسار مرفوض من حاجز الأمان — لا يُستخدم في أي عملية قرص."""


def sanitize_for_log(value: object, limit: int = 200) -> str:
    """يطهّر قيمة قبل كتابتها في السجل — يمنع Log Injection (CWE-117).

    `.replace("\\n", …)` و`.replace("\\r\\n", …)` مباشرة هي الـ sanitizer الذي
    يعترف به CodeQL (ReplaceLineBreaksSanitizer). ثم تُزال بقية محارف التحكّم
    عبر isprintable()، ويُقتطع الطول.
    """
    text = str(value)
    text = text.replace("\r\n", "⏎").replace("\n", "⏎").replace("\r", "⏎")
    safe = "".join(ch for ch in text if ch.isprintable())
    return safe[:limit]


def guard_path(
    raw: str | os.PathLike[str],
    *,
    allowed_roots: tuple[Path, ...] | None = None,
    must_stay_under_root: bool = False,
) -> Path:
    """يتحقق من مسار قادم من مصدر غير موثوق ويعيده كـ `Path` آمن (مُطلق ومُطبَّع).

    المعاملات:
      raw: المسار الخام (من text_input / env / CLI).
      allowed_roots: جذور يُسمح بالبقاء تحتها. عند None تُستخدم الجذور الافتراضية.
      must_stay_under_root: محفوظ للتوافق؛ السلوك الافتراضي يفرض البقاء تحت
                           جذور مسموحة على أي حال (أأمن).

    يرفع `UnsafePath` عند:
      • فراغ/طول مفرط · محرف NUL · محاولة traversal (`..`)
      • محارف غير مسموحة (رموز أوامر/أسطار جديدة/تحكّم)
      • خروج عن كل الجذور المسموحة

    ملاحظة CodeQL: القيمة المُعادة هي `normalized` — نفس المتغيّر الذي مرّ على
    PathNormalization (abspath) ثم اجتاز SafeAccessCheck (startswith allowlist).
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

    # فحوصات بلا regex على الإطلاق ⇒ لا py/polynomial-redos.
    if ".." in text.replace("\\", "/").split("/"):
        raise UnsafePath("محاولة traversal مرفوضة (..).")
    if not all(ch.isalnum() or ch in _ALLOWED_PUNCT for ch in text):
        raise UnsafePath("المسار يحتوي محارف غير مسموحة.")

    # (1) PathNormalization — CodeQL: NotNormalized → NormalizedUnchecked.
    normalized = os.path.abspath(text)

    # (2) SafeAccessCheck — CodeQL: startswith على المتغيّر المطبَّع بـ branch=true.
    roots = (
        tuple(os.path.abspath(str(r)) for r in allowed_roots)
        if (must_stay_under_root and allowed_roots)
        else _default_roots()
    )
    for root in roots:
        if normalized == root or normalized.startswith(root + os.sep):
            # normalized اجتاز SafeAccessCheck ⇒ الـ taint مقطوع هنا.
            return Path(normalized)
    raise UnsafePath("المسار خارج الجذور المسموحة.")


def guard_run_id(run_id: str) -> str:
    """يتحقق من مُعرِّف تشغيل قبل بناء اسم ملف منه (نمط store.py).

    يقبل فقط: حرف/رقم أولاً، ثم حروف وأرقام ونقاط وشرطات — حتى 128 محرفاً.
    يمنع `/` و`..` وحقن المسار في `self.path / f"{run_id}.json"`.
    """
    if not isinstance(run_id, str) or not run_id:
        raise UnsafePath("معرِّف تشغيل فارغ.")
    if len(run_id) > 128:
        raise UnsafePath("معرِّف تشغيل أطول من الحد.")
    # فحص يدوي بلا regex (يتجنب polynomial-redos على قيمة مستخدم).
    if not (run_id[0].isascii() and (run_id[0].isalnum())):
        raise UnsafePath("معرِّف تشغيل غير صالح؛ ابدأ بحرف أو رقم.")
    if not all(ch.isascii() and (ch.isalnum() or ch in "._-") for ch in run_id):
        raise UnsafePath("معرِّف تشغيل غير صالح؛ استخدم حروفاً وأرقاماً و._- فقط.")
    return run_id


__all__ = ["UnsafePath", "guard_path", "guard_run_id", "sanitize_for_log"]

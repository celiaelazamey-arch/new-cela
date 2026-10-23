"""
الأدوات المدمجة (Built-in Tools)
================================
أدوات P0 الحقيقية — تعمل بلا شبكة وبلا مفاتيح:

| الأداة | الخطر | النطاق | ما تفعله |
|---|---|---|---|
| `read_file` | read | `fs.read` | تقرأ ملفاً نصياً داخل الجذور المسموحة |
| `search_files` | read | `fs.read` | تبحث عن نص في ملفات المشروع وتعيد السطر والسياق |
| `replace_text` | write | `fs.write` | استبدال محدود وذرّي بشرط البصمة وعدد المواضع |
| `run_command` | execute | `shell.exec` | تشغيل أمر من **قائمة بيضاء** عبر SandboxExecutor |
| `ocr_extract` | read | `fs.read` | تستخرج نصاً من صورة/PDF داخل الجذور المسموحة |

قاعدة أمان: أدوات الملفات و`cwd` لا تخرج عن `PolicyEngine.allowed_roots` مهما كان
المسار المُمرَّر — والتحقق مزدوج (في السجل وفي الأداة)، والكتابة/التنفيذ بموافقة.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import stat
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.policy import PolicyDenied, ToolRisk
from src.execution.sandbox import SandboxExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolResult, ToolSpec

logger = logging.getLogger("new-cela.tools.builtin")

MAX_READ_CHARS = 20_000
MAX_SEARCH_RESULTS = 20
MAX_SEARCH_FILES = 400
SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".ruff_cache"}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".sql", ".sh", ".csv",
    ".env", ".example", ".gitignore", ".dockerfile",
}


# ================= read_file =================
class ReadFileParams(BaseModel):
    """وسائط قراءة ملف."""

    path: str = Field(..., min_length=1, description="مسار الملف (نسبي للجذر المسموح)")
    max_chars: int = Field(default=4000, ge=1, le=MAX_READ_CHARS)


async def read_file(params: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(params, ReadFileParams)
    resolved = ctx.policy.resolve_path(params.path)
    if not resolved.exists():
        raise FileNotFoundError(f"الملف غير موجود: {params.path}")
    if resolved.is_dir():
        raise IsADirectoryError(f"المسار مجلد لا ملف: {params.path}")

    data = await asyncio.to_thread(resolved.read_bytes)
    if b"\x00" in data[:2048]:
        raise ValueError(f"ملف ثنائي غير مدعوم: {params.path}")

    text = data.decode("utf-8", errors="replace")[: params.max_chars]
    suffix = "" if len(data) <= params.max_chars else "\n… (اقتُطع)"
    return f"# {resolved.name} ({len(data)} بايت)\n{text}{suffix}"


# ================= search_files =================
class SearchFilesParams(BaseModel):
    """وسائط البحث النصي في الملفات."""

    query: str = Field(..., min_length=1, description="النص المطلوب البحث عنه")
    path: str = Field(default=".", description="المجلد الجذر للبحث")
    max_results: int = Field(default=10, ge=1, le=MAX_SEARCH_RESULTS)
    case_sensitive: bool = False


async def search_files(params: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(params, SearchFilesParams)
    root = ctx.policy.resolve_path(params.path)
    if not root.exists():
        raise FileNotFoundError(f"المسار غير موجود: {params.path}")

    needle = params.query if params.case_sensitive else params.query.lower()
    hits: list[str] = []
    scanned = 0

    for file in _iter_files(root):
        scanned += 1
        if scanned > MAX_SEARCH_FILES:
            break
        try:
            text = await asyncio.to_thread(
                file.read_text, encoding="utf-8", errors="replace"
            )
        except OSError:
            continue

        haystack = text if params.case_sensitive else text.lower()
        if needle not in haystack:
            continue

        for number, line in enumerate(text.splitlines(), start=1):
            candidate = line if params.case_sensitive else line.lower()
            if needle in candidate:
                rel = _relative(file, ctx)
                hits.append(f"{rel}:{number}: {line.strip()[:160]}")
                break
        if len(hits) >= params.max_results:
            break

    if not hits:
        return f"لا نتائج لـ «{params.query}» (فُحص {scanned} ملفاً)."
    return f"نتائج «{params.query}» ({len(hits)}):\n" + "\n".join(hits)


def _iter_files(root: Path):
    """يمشي على الملفات النصية فقط مع تجاوز مجلدات الضجيج."""
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith("."):
            yield path


def _relative(path: Path, ctx: ToolContext) -> str:
    for root in ctx.policy.allowed_roots:
        if path == root or root in path.parents:
            try:
                return str(path.relative_to(root))
            except ValueError:
                continue
    return str(path)


# ================= run_command =================
class RunCommandParams(BaseModel):
    """وسائط تنفيذ أمر — لا shell، وقائمة بيضاء في الصندوق الرملي."""

    command: str = Field(..., min_length=1, description="أمر من القائمة البيضاء فقط")
    timeout: int = Field(default=30, ge=1, le=120)
    cwd: str = Field(default=".", description="مجلد التشغيل داخل الجذور المسموحة")


async def run_command(params: BaseModel, ctx: ToolContext) -> ToolResult:
    """يعيد رمز الخروج والمخرجات حتى عند فشل اختبار؛ الفشل قياس لا انهيار."""
    assert isinstance(params, RunCommandParams)
    cwd = ctx.policy.resolve_path(params.cwd)
    if not cwd.is_dir():
        raise NotADirectoryError(f"مجلد التشغيل غير موجود: {params.cwd}")
    executor = SandboxExecutor(timeout=min(params.timeout, ctx.timeout))
    result = await executor.run(params.command, cwd=cwd)
    output = (result.stdout or "").strip()
    error = ""
    if result.returncode != 0:
        error = f"رمز الخروج {result.returncode}: {(result.stderr or output)[:500]}"
    return ToolResult(
        name="run_command",
        ok=result.returncode == 0,
        output=output[:4000] or ("(بلا مخرجات)" if result.returncode == 0 else ""),
        error=error,
        reason="command_failed" if error else "",
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
    )


# ================= replace_text =================
class ReplaceTextParams(BaseModel):
    """استبدال حتمي ومشروط بالبصمة؛ لا ينفّذ نصاً ولا يكتب خارج الجذر."""

    path: str = Field(..., min_length=1)
    old: str = Field(..., min_length=1)
    new: str = ""
    expected_occurrences: int = Field(default=1, ge=1, le=100)
    expected_sha256: str = Field(default="", pattern=r"^$|^[0-9a-f]{64}$")


async def replace_text(params: BaseModel, ctx: ToolContext) -> str:
    """يعدّل ملفاً نصياً ذرّياً بعد إثبات عدد المواضع وبصمة النسخة المقروءة."""
    assert isinstance(params, ReplaceTextParams)
    if params.old == params.new:
        raise ValueError("الاستبدال بلا أثر: النصان متطابقان.")
    resolved = ctx.policy.resolve_path(params.path)
    if not resolved.is_file():
        raise FileNotFoundError(f"الملف غير موجود: {params.path}")
    original_stat = resolved.stat()
    data = await asyncio.to_thread(resolved.read_bytes)
    if b"\x00" in data[:2048]:
        raise ValueError(f"ملف ثنائي غير مدعوم: {params.path}")
    before = data.decode("utf-8")
    before_hash = hashlib.sha256(data).hexdigest()
    if params.expected_sha256 and params.expected_sha256 != before_hash:
        raise RuntimeError(
            f"تغيّر الملف منذ القراءة: المتوقع {params.expected_sha256}، الفعلي {before_hash}."
        )
    occurrences = before.count(params.old)
    if occurrences != params.expected_occurrences:
        raise ValueError(
            f"عدد مواضع النص {occurrences}، بينما المطلوب {params.expected_occurrences}."
        )
    after = before.replace(params.old, params.new)
    payload = after.encode("utf-8")
    after_hash = hashlib.sha256(payload).hexdigest()

    descriptor, temporary_name = tempfile.mkstemp(prefix=".replace-", dir=resolved.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(original_stat.st_mode))
        os.replace(temporary, resolved)
        # Python's pyc validator uses whole-second mtime + size. A same-size
        # edit within one second must not leave a stale, apparently valid cache.
        replaced_stat = resolved.stat()
        if int(replaced_stat.st_mtime) <= int(original_stat.st_mtime):
            os.utime(
                resolved,
                ns=(replaced_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
            )
    finally:
        temporary.unlink(missing_ok=True)
    return (
        f"{params.path}: replacements={occurrences}\n"
        f"before_sha256={before_hash}\nafter_sha256={after_hash}"
    )


# ================= ocr_extract =================
class OcrExtractParams(BaseModel):
    """وسائط استخراج نص من صورة أو PDF."""

    path: str = Field(..., min_length=1, description="مسار الصورة أو ملف PDF")
    lang: str = Field(default="ara+eng", description="لغة Tesseract (مثال ara+eng)")
    max_chars: int = Field(default=8000, ge=100, le=20000)


async def ocr_extract(params: BaseModel, ctx: ToolContext) -> str:
    """يقرأ النص من صورة/PDF — قراءة فقط، وبلا أي كتابة على المصدر."""
    assert isinstance(params, OcrExtractParams)
    from src.ocr.engine import OcrError, extract_text

    resolved = ctx.policy.resolve_path(params.path)
    try:
        result = await asyncio.to_thread(extract_text, resolved, None, params.lang)
    except OcrError as exc:
        raise RuntimeError(f"تعذّر استخراج النص ({type(exc).__name__}): {exc}") from exc

    if result.empty:
        warning = "؛ ".join(result.warnings) or "لا نص في الملف"
        raise RuntimeError(f"لم يُستخرج نص ({result.backend}): {warning}")

    header = f"[{result.backend}] {resolved.name}"
    if result.pages:
        header += f" — {result.pages} صفحة"
    return f"{header}\n{result.text[: params.max_chars]}"


# ================= التسجيل =================
def build_default_registry(ctx: ToolContext | None = None) -> ToolRegistry:
    """
    يسجّل الأدوات المدمجة على سياسة/ميزانية معيّنة.

    بدون تمرير سياق: سياسة محافظة (قراءة داخل مجلد العمل، رفض كل ما يحتاج موافقة).
    """
    from src.core.policy import PolicyEngine

    policy = ctx.policy if ctx is not None else PolicyEngine()
    registry = ToolRegistry(policy=policy, quota=ctx.quota if ctx is not None else None)

    registry.register(
        ToolSpec(
            name="read_file",
            description="قراءة ملف نصي من مساحة العمل",
            params=ReadFileParams,
            handler=read_file,
            risk=ToolRisk.READ,
            scope="fs.read",
            path_params=("path",),
            examples=('{"path": "README.md"}',),
        )
    )
    registry.register(
        ToolSpec(
            name="search_files",
            description="بحث نصي في ملفات مساحة العمل",
            params=SearchFilesParams,
            handler=search_files,
            risk=ToolRisk.READ,
            scope="fs.read",
            path_params=("path",),
            examples=('{"query": "QuotaGuard"}',),
        )
    )
    registry.register(
        ToolSpec(
            name="ocr_extract",
            description="استخراج نص من صورة أو PDF ممسوح (OCR محلي مجاني) — قراءة فقط",
            params=OcrExtractParams,
            handler=ocr_extract,
            risk=ToolRisk.READ,
            scope="fs.read",
            path_params=("path",),
            examples=('{"path": "scan.png", "lang": "ara+eng"}',),
        )
    )
    registry.register(
        ToolSpec(
            name="replace_text",
            description="استبدال نصي محدود داخل ملف، ببصمة قبل/بعد وكتابة ذرّية — يتطلب موافقة",
            params=ReplaceTextParams,
            handler=replace_text,
            risk=ToolRisk.WRITE,
            scope="fs.write",
            path_params=("path",),
            examples=('{"path": "app.py", "old": "return 1", "new": "return 2"}',),
        )
    )
    registry.register(
        ToolSpec(
            name="run_command",
            description="تنفيذ أمر من قائمة بيضاء آمنة (بلا shell) — يتطلب موافقة",
            params=RunCommandParams,
            handler=run_command,
            risk=ToolRisk.EXECUTE,
            scope="shell.exec",
            path_params=("cwd",),
            examples=('{"command": "python --version", "cwd": "."}',),
        )
    )
    return registry


__all__ = [
    "OcrExtractParams",
    "PolicyDenied",
    "ReadFileParams",
    "ReplaceTextParams",
    "RunCommandParams",
    "SearchFilesParams",
    "build_default_registry",
    "ocr_extract",
    "read_file",
    "replace_text",
    "run_command",
    "search_files",
]

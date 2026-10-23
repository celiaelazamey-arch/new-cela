"""
الأدوات المدمجة (Built-in Tools)
================================
أدوات P0 الحقيقية — تعمل بلا شبكة وبلا مفاتيح:

| الأداة | الخطر | النطاق | ما تفعله |
|---|---|---|---|
| `read_file` | read | `fs.read` | تقرأ ملفاً نصياً داخل الجذور المسموحة |
| `search_files` | read | `fs.read` | تبحث عن نص في ملفات المشروع وتعيد السطر والسياق |
| `run_command` | execute | `shell.exec` | تشغيل أمر من **قائمة بيضاء** عبر SandboxExecutor |

قاعدة أمان: `read_file` و`search_files` لا تخرجان عن `PolicyEngine.allowed_roots`
مهما كان المسار المُمرَّر — والتحقق مزدوج (في السجل وفي الأداة).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.policy import PolicyDenied, ToolRisk
from src.execution.sandbox import SandboxExecutor
from src.tools.registry import ToolContext, ToolRegistry, ToolSpec

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


async def run_command(params: BaseModel, ctx: ToolContext) -> str:
    assert isinstance(params, RunCommandParams)
    executor = SandboxExecutor(timeout=min(params.timeout, ctx.timeout))
    result = await executor.run(params.command)
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(f"رمز الخروج {result.returncode}: {(result.stderr or output)[:500]}")
    return output[:4000] or "(بلا مخرجات)"


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
            name="run_command",
            description="تنفيذ أمر من قائمة بيضاء آمنة (بلا shell) — يتطلب موافقة",
            params=RunCommandParams,
            handler=run_command,
            risk=ToolRisk.EXECUTE,
            scope="shell.exec",
            examples=('{"command": "python --version"}',),
        )
    )
    return registry


__all__ = [
    "PolicyDenied",
    "ReadFileParams",
    "RunCommandParams",
    "SearchFilesParams",
    "build_default_registry",
    "read_file",
    "run_command",
    "search_files",
]

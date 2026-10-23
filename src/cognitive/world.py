"""نموذج عالم تشغيلي: بصمات ملفات وحالة Git ونتائج أوامر فعلية.

اللقطة لا «تفهم» البيئة؛ تصف فقط ما قُرئ وقيس. وهي حتمية ولا تحمل طابعاً
زمنياً، لذلك يمكن مقارنة لقطتين أو إعادة قرار من الحالة نفسها.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from pydantic import BaseModel, Field

from src.cognitive._utils import digest_json
from src.tools.registry import ToolResult


class CommandObservation(BaseModel):
    """أثر أمر نُفِّذ فعلاً، بما فيه فشل الاختبار ورمز خروجه."""

    command: str
    cwd: str = "."
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @classmethod
    def from_tool(cls, command: str, result: ToolResult) -> CommandObservation:
        if result.returncode is None:
            raise ValueError("لا توجد ملاحظة أمر: الأداة لم تُنتج رمز خروج.")
        return cls(
            command=command,
            returncode=result.returncode,
            stdout=result.stdout or (result.output if result.ok else ""),
            stderr=result.stderr or ("" if result.ok else result.error),
            timed_out=result.timed_out,
        )

    def render(self, limit: int = 4000) -> str:
        output = "\n".join(part for part in (self.stdout, self.stderr) if part).strip()
        if len(output) > limit:
            output = output[: limit - 1] + "…"
        return f"$ {self.command}\nexit={self.returncode}\n{output}".rstrip()


class WorldSnapshot(BaseModel):
    """لقطة محدودة بالجذر والمسارات المطلوبة؛ ليست مسحاً عشوائياً للقرص."""

    root: str
    git_sha: str = ""
    files: dict[str, str] = Field(default_factory=dict)
    commands: list[CommandObservation] = Field(default_factory=list)

    def fingerprint(self) -> str:
        return digest_json(self.model_dump(mode="json"))

    def command(self, command: str) -> CommandObservation | None:
        """آخر تشغيل للأمر؛ التكرار يعبّر عن قبل/بعد لا عن ازدواج غامض."""
        for observation in reversed(self.commands):
            if observation.command == command:
                return observation
        return None


class FileChange(BaseModel):
    before: str = ""
    after: str = ""


class WorldDiff(BaseModel):
    """فرق قابل للتفسير بين لقطتين، بلا استنتاجات سببية غير مثبتة."""

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: dict[str, FileChange] = Field(default_factory=dict)
    unchanged: list[str] = Field(default_factory=list)
    git_before: str = ""
    git_after: str = ""

    @property
    def has_file_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def changed_paths(self) -> list[str]:
        return sorted([*self.added, *self.removed, *self.changed])


class WorldModel:
    """يقرأ لقطات داخل جذر واحد ويمنع الهروب عبر `..` والروابط الرمزية."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()

    def capture(
        self,
        paths: list[str] = (),
        commands: list[CommandObservation] = (),
    ) -> WorldSnapshot:
        fingerprints: dict[str, str] = {}
        for raw in sorted(set(paths)):
            resolved = self.resolve(raw)
            if resolved.is_file():
                fingerprints[raw] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        return WorldSnapshot(
            root=str(self.root),
            git_sha=self._git_sha(),
            files=fingerprints,
            commands=list(commands),
        )

    def resolve(self, raw: str) -> Path:
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError(f"المسار خارج جذر نموذج العالم: {raw}")
        return resolved

    def diff(self, before: WorldSnapshot, after: WorldSnapshot) -> WorldDiff:
        before_files = before.files
        after_files = after.files
        added = sorted(after_files.keys() - before_files.keys())
        removed = sorted(before_files.keys() - after_files.keys())
        changed = {
            path: FileChange(before=before_files[path], after=after_files[path])
            for path in sorted(before_files.keys() & after_files.keys())
            if before_files[path] != after_files[path]
        }
        unchanged = sorted(
            path
            for path in before_files.keys() & after_files.keys()
            if before_files[path] == after_files[path]
        )
        return WorldDiff(
            added=added,
            removed=removed,
            changed=changed,
            unchanged=unchanged,
            git_before=before.git_sha,
            git_after=after.git_sha,
        )

    def _git_sha(self) -> str:
        """يقرأ HEAD والمرجع مباشرة؛ لا يشغّل أمراً خارج بوابة الموافقات."""
        try:
            git_dir = (self.root / ".git").resolve()
            if self.root not in git_dir.parents:
                return ""
            head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref:"):
                return head
            ref = head.split(":", 1)[1].strip()
            ref_path = (git_dir / ref).resolve()
            if git_dir not in ref_path.parents:
                return ""
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8").strip()
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split(" ", 1)[0]
        except (OSError, RuntimeError):
            return ""
        return ""


__all__ = [
    "CommandObservation",
    "FileChange",
    "WorldDiff",
    "WorldModel",
    "WorldSnapshot",
]

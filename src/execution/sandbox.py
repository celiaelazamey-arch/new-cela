"""
منفذ الأوامر المعزول (Code Sandbox)
===================================
تنفيذ أوامر النظام بأمان نسبي:
  • بدون shell (create_subprocess_exec) — يمنع حقن الأوامر (Command Injection)
  • قائمة بيضاء للأوامر المسموحة فقط
  • مهلة زمنية إلزامية وحد أقصى لحجم الإخراج
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass

logger = logging.getLogger("new-cela.sandbox")

ALLOWED_COMMANDS = (
    "python",
    "python3",
    "pip",
    "pytest",
    "git",
    "ls",
    "cat",
    "echo",
    "ruff",
    "which",
)


@dataclass
class ExecResult:
    """نتيجة تنفيذ أمر واحد."""

    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    def summary(self) -> str:
        status = "مهلة منتهية ⏱️" if self.timed_out else f"رمز الخروج {self.returncode}"
        return f"$ {self.command}\n[{status}]\n{self.stdout}{self.stderr}".strip()


class SandboxExecutor:
    """منفذ أوامر آمن معزول."""

    def __init__(self, timeout: int = 60, max_output_chars: int = 20_000) -> None:
        self.timeout = timeout
        self.max_output_chars = max_output_chars

    def _validate(self, command: str) -> list[str]:
        args = shlex.split(command)
        if not args:
            raise ValueError("الأمر فارغ.")
        if args[0] not in ALLOWED_COMMANDS:
            raise PermissionError(
                f"الأمر '{args[0]}' غير مسموح به. المسموح: {', '.join(ALLOWED_COMMANDS)}"
            )
        return args

    async def run(self, command: str) -> ExecResult:
        """ينفّذ أمراً من القائمة البيضاء ويعيد النتيجة المهيكلة."""
        args = self._validate(command)
        logger.info("تنفيذ أمر معزول: %s", command[:100])

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            logger.warning("أمر تجاوز المهلة وتم إنهاؤه: %s", command[:100])
            return ExecResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"تم إنهاء الأمر لتجاوز المهلة ({self.timeout} ثانية).",
                timed_out=True,
            )

        out = stdout.decode("utf-8", errors="replace")[: self.max_output_chars]
        err = stderr.decode("utf-8", errors="replace")[: self.max_output_chars]
        return ExecResult(
            command=command,
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=out,
            stderr=err,
        )

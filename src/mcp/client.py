"""
عميل MCP عبر stdio — تشغيل خادم، مصافحة، قائمة أدوات، واستدعاء
=============================================================
كل تفاعل هنا **محكوم بحدود**:

  • مهلة لكل طلب (افتراضياً 10 ثوانٍ) — لا تعليق أبدي على خادم صامت.
  • بيئة منظّفة: نمرّر PATH/HOME/LANG/TMPDIR فقط + ما يصرّح به الإعداد.
    مفاتيح النموذج (GEMINI_API_KEY…) **لا تُمرَّر** افتراضياً إلى خادم خارجي.
  • stderr يُقرأ في خيط منفصل (منع الامتلاء والجمود)، ويُحفظ ذيله للتشخيص.
  • `close()` يُنهي العملية ثم يقتلها إن لم تستجب.

الخادم عملية خارجية غير موثوقة: تشغيله يتطلب موافقة صريحة في طبقة السياسة
(`mcp.call` داخل قائمة النطاقات التي تُسأل دائماً).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from collections.abc import Sequence
from typing import Self

from src.mcp.protocol import (
    McpError,
    McpProtocolError,
    McpServerClosed,
    McpTimeout,
    McpTool,
    build_notification,
    build_request,
    error_from,
    initialise_params,
    is_error_result,
    parse_message,
    parse_tools,
    text_from_result,
    valid_tool_name,
)

logger = logging.getLogger("new-cela.mcp")

DEFAULT_TIMEOUT = 10.0
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "TEMP")
MAX_STDERR_LINES = 40


def sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """
    بيئة الحد الأدنى للخادم.

    أي متغير غير مذكور هنا (بما فيه مفاتيح API) لا يصل إلى العملية الابنة.
    """
    env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    env.setdefault("PYTHONUNBUFFERED", "1")
    for key, value in (extra or {}).items():
        env[str(key)] = str(value)
    return env


class StdioMcpClient:
    """عميل خادم MCP واحد — يبدأ وينتهي بصراحة، ولا يخفي حالة الاتصال."""

    def __init__(
        self,
        command: Sequence[str],
        *,
        name: str = "server",
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not command:
            raise ValueError("أمر تشغيل الخادم مطلوب.")
        self.command = list(command)
        self.name = name
        self.cwd = cwd
        self.env = sanitized_env(env)
        self.timeout = float(timeout)

        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stderr: list[str] = []
        self._next_id = 0
        self.server_info: dict = {}
        self.protocol_version = ""

    # ---------------- دورة الحياة ----------------
    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self._proc is not None:
            return
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
                text=True,
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise McpServerClosed(
                f"تعذّر تشغيل الخادم «{self.name}»: {type(exc).__name__}: {exc}"
            ) from exc

        threading.Thread(target=self._pump_stdout, name=f"mcp-out-{self.name}", daemon=True).start()
        threading.Thread(target=self._pump_stderr, name=f"mcp-err-{self.name}", daemon=True).start()
        logger.info("بدأ خادم MCP: %s", self.name)

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.stdin and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError as exc:  # مقصود — العملية قد تكون أغلقت stdin بالفعل
                logger.debug("تعذّر إغلاق stdin لخادم MCP: %s", type(exc).__name__)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:  # مقصود — تصعيد الإنهاء عند رفض الخروج
            logger.warning("خادم MCP لم يستجب للإنهاء — إرسال terminate.")
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:  # مقصود — قتل نهائي بعد استنفاد المهلة
                logger.error("خادم MCP رفض الإنهاء بعد terminate — قتل العملية.")
                proc.kill()
                proc.wait(timeout=2)
        logger.info("أُغلق خادم MCP: %s", self.name)

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr[-MAX_STDERR_LINES:])

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---------------- القراءة الخلفية ----------------
    def _pump_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._queue.put(None)
            return
        try:
            for line in proc.stdout:
                self._queue.put(line)
        finally:
            self._queue.put(None)

    def _pump_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > MAX_STDERR_LINES * 2:
                del self._stderr[:MAX_STDERR_LINES]

    # ---------------- الإرسال ----------------
    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpServerClosed(
                f"الخادم «{self.name}» غير قيد التشغيل."
                + (f"\nstderr: {self.stderr_tail}" if self._stderr else "")
            )
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpServerClosed(f"تعذّر الإرسال إلى «{self.name}»: {exc}") from exc

    def _read_line(self, timeout: float) -> str:
        try:
            item = self._queue.get(timeout=max(0.01, timeout))
        except queue.Empty as exc:
            raise McpTimeout(
                f"انتهت المهلة ({timeout:.1f}s) في انتظار ردّ «{self.name}»."
            ) from exc
        if item is None:
            raise McpServerClosed(
                f"أُغلق الخادم «{self.name}» قبل الردّ."
                + (f"\nstderr: {self.stderr_tail}" if self._stderr else "")
            )
        return item

    # ---------------- الطلبات ----------------
    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        """يُرسل طلباً وينتظر الردّ المطابق للمُعرِّف، متجاهلاً الإشعارات."""
        self._next_id += 1
        request_id = self._next_id
        self._send(build_request(request_id, method, params))

        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpTimeout(
                    f"انتهت المهلة في «{self.name}» عند {method}."
                )
            payload = parse_message(self._read_line(remaining))
            if payload.get("id") != request_id:
                if "id" not in payload:
                    continue  # إشعار/سجلّ من الخادم — لا يخصّنا
                raise McpProtocolError(
                    f"ردّ بمُعرِّف غير متوقع: {payload.get('id')} (المتوقع {request_id})."
                )
            if "error" in payload:
                raise error_from(payload["error"])
            result = payload.get("result")
            if not isinstance(result, dict):
                raise McpProtocolError("ردّ بلا كائن result.")
            return result

    # ---------------- العمليات ----------------
    def initialize(self) -> dict:
        """مصافحة MCP: initialize ثم إشعار initialized (إلزامي في البروتوكول)."""
        result = self.request("initialize", initialise_params())
        self.server_info = result.get("serverInfo") or {}
        self.protocol_version = str(result.get("protocolVersion") or "")
        self._send(build_notification("notifications/initialized"))
        return result

    def list_tools(self) -> list[McpTool]:
        """كل الأدوات المعلنة، مع دعم الترقيم (cursor) بحدّ أقصى للحماية."""
        tools: list[McpTool] = []
        cursor: str | None = None
        for _ in range(5):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            tools.extend(parse_tools(result))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools[:50]

    def call_tool(self, name: str, arguments: dict | None = None, timeout: float | None = None) -> str:
        """ينفّذ أداة على الخادم ويعيد نصها — ويرفع `McpError` عند isError."""
        if not valid_tool_name(name):
            raise McpError(f"اسم أداة غير صالح: {name!r}")
        result = self.request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            timeout=timeout,
        )
        text = text_from_result(result)
        if is_error_result(result):
            raise McpError(text or "أبلغ الخادم عن فشل التنفيذ بلا نص.")
        return text

    def describe(self) -> dict:
        """وصف قابل للتسجيل — بلا أي متغير بيئة أو مسار حسّاس."""
        return {
            "name": self.name,
            "command": " ".join(self.command),
            "running": self.running,
            "protocol_version": self.protocol_version,
            "server": self.server_info,
        }
